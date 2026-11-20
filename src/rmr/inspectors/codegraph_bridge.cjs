"use strict";

const path = require("node:path");
const fs = require("node:fs");

function location(node) {
    return { name: node.name, kind: node.kind, file: node.filePath, line: node.startLine };
}

function safeNode(root, node) {
    if (!node || !node.filePath || !Number.isInteger(node.startLine) || node.startLine < 1) return false;
    try {
        const realRoot = fs.realpathSync(root);
        const realFile = fs.realpathSync(path.resolve(root, node.filePath));
        const relative = path.relative(realRoot, realFile);
        return relative !== "" && !relative.startsWith(".." + path.sep) && relative !== ".." &&
            !path.isAbsolute(relative) && fs.statSync(realFile).isFile();
    } catch { return false; }
}

function impact(graph, groups, root, depth, limit) {
    const key = n => JSON.stringify([n.filePath, n.startLine, n.name, n.kind, n.id]);
    groups = groups.map(g => g.filter(n => safeNode(root, n)).sort((a, b) => key(a).localeCompare(key(b))))
        .filter(g => g.length).sort((a, b) => key(a[0]).localeCompare(key(b[0])));
    const ambiguous = groups.length > 1;
    let truncated = groups.length > limit;
    const definitions = groups.map(group => ({ definition: location(group[0]), affected: [] }));
    const candidates = [];
    groups.forEach((group, groupIndex) => {
        const nodes = new Map();
        const edges = [];
        for (const focal of group) {
            const subgraph = graph.getImpactRadius(focal.id, depth);
            for (const [id, node] of subgraph.nodes) if (safeNode(root, node)) nodes.set(id, node);
            edges.push(...subgraph.edges);
        }
        const adjacency = new Map();
        for (const edge of edges) {
            // Containment follows the container to its members without adding dependency depth.
            const from = edge.kind === "contains" ? edge.source : edge.target;
            const to = edge.kind === "contains" ? edge.target : edge.source;
            if (!nodes.has(from) || !nodes.has(to)) continue;
            if (!adjacency.has(from)) adjacency.set(from, []);
            adjacency.get(from).push({ to, relation: edge.kind, weight: edge.kind === "contains" ? 0 : 1 });
        }
        for (const links of adjacency.values()) links.sort((a, b) =>
            key(nodes.get(a.to)).localeCompare(key(nodes.get(b.to))) || a.relation.localeCompare(b.relation));
        const best = new Map();
        const previous = new Map();
        const settled = new Set();
        const buckets = Array.from({ length: depth + 1 }, () => []);
        const roots = new Set(group.map(n => n.id));
        for (const node of group) {
            best.set(node.id, 0);
            buckets[0].push(node.id);
        }
        // With weights restricted to 0 and 1, bounded FIFO buckets visit each
        // node at its minimum dependency distance without repeatedly sorting paths.
        for (let distance = 0; distance <= depth; distance++) {
            const bucket = buckets[distance];
            for (let cursor = 0; cursor < bucket.length; cursor++) {
                const id = bucket[cursor];
                if (settled.has(id) || best.get(id) !== distance) continue;
                settled.add(id);
                if (!roots.has(id)) candidates.push({ groupIndex, distance, id, nodes, previous });
                for (const link of adjacency.get(id) || []) {
                    const nextDistance = distance + link.weight;
                    if (nextDistance > depth || (best.has(link.to) && best.get(link.to) <= nextDistance)) continue;
                    best.set(link.to, nextDistance);
                    previous.set(link.to, { id, relation: link.relation });
                    buckets[nextDistance].push(link.to);
                }
            }
        }
    });
    candidates.sort((a, b) => a.distance - b.distance || a.groupIndex - b.groupIndex ||
        key(a.nodes.get(a.id)).localeCompare(key(b.nodes.get(b.id))));
    const nearest = new Map();
    for (const candidate of candidates) {
        if (!nearest.has(candidate.groupIndex)) nearest.set(candidate.groupIndex, candidate.distance);
    }
    const rankedGroups = definitions.map((_, index) => ({ index, nearest: nearest.get(index) ?? Infinity }));
    rankedGroups.sort((a, b) => a.nearest - b.nearest || a.index - b.index);
    const selected = new Set(rankedGroups.slice(0, limit).map(group => group.index));
    const eligible = candidates.filter(candidate => selected.has(candidate.groupIndex));
    if (eligible.length > limit) truncated = true;
    for (const candidate of eligible.slice(0, limit)) {
        const path = [];
        let id = candidate.id;
        while (true) {
            const predecessor = candidate.previous.get(id);
            path.push({ ...location(candidate.nodes.get(id)), ...(predecessor && { relation: predecessor.relation }) });
            if (!predecessor) break;
            id = predecessor.id;
        }
        path.reverse();
        definitions[candidate.groupIndex].affected.push({ ...location(candidate.nodes.get(candidate.id)),
            depth: candidate.distance, path });
    }
    const resultDefinitions = definitions.filter((_, index) => selected.has(index));
    return { status: resultDefinitions.length ? "ok" : "not_found", definitions: resultDefinitions, ambiguous, truncated };
}

function trace(install, root, database, mode, symbol, file, limit, depth) {
    const manifest = require(path.join(install, "lib", "package.json"));
    if (manifest.name !== "@colbymchenry/codegraph" || manifest.version !== "1.6.2") {
        return { status: "unsupported_version" };
    }

    const dist = path.join(install, "lib", "dist");
    const { CodeGraph, DatabaseConnection, QueryBuilder } = require(dist);
    const { lookupSymbolNodes, groupDefinitions } = require(path.join(dist, "graph", "symbol-lookup"));
    // The standard CLI searches ancestor indexes and can run database migrations.
    const connection = DatabaseConnection.open(database, { readOnly: true });
    try {
        const queries = new QueryBuilder(connection.getDb());
        const graph = new CodeGraph(connection, queries, root);
        const { nodes } = lookupSymbolNodes(graph, symbol);
        const targetFile = file ? path.resolve(root, file) : null;
        const targets = targetFile
            ? nodes.filter(node => path.relative(targetFile, path.resolve(root, node.filePath)) === "")
            : nodes;
        const { groups } = groupDefinitions(targets);
        if (mode === "impact") return impact(graph, groups, root, depth, limit);
        const definitions = [];
        const callers = mode === "callers";
        let remaining = limit;
        let truncated = groups.length > limit;
        for (const group of groups.slice(0, limit)) {
            const linked = new Map();
            for (const node of group) {
                const kinds = ["calls", "instantiates"];
                const edges = callers
                    ? queries.getIncomingEdges(node.id, kinds)
                    : queries.getOutgoingEdges(node.id, kinds);
                for (const edge of edges) {
                    const id = callers ? edge.source : edge.target;
                    const key = JSON.stringify([id, edge.kind, edge.line]);
                    if (linked.has(key)) continue;
                    if (linked.size === remaining) {
                        truncated = true;
                        break;
                    }
                    linked.set(key, { id, relation: edge.kind, call_line: edge.line ?? null });
                }
            }
            const relatedNodes = queries.getNodesByIds([...new Set([...linked.values()].map(item => item.id))]);
            const related = [];
            for (const { id, relation, call_line } of linked.values()) {
                const node = relatedNodes.get(id);
                if (node) related.push({ ...location(node), relation, call_line });
            }
            remaining -= related.length;
            definitions.push({ definition: location(group[0]), related });
        }
        return { status: definitions.length ? "ok" : "not_found", definitions, truncated };
    } finally {
        connection.close();
    }
}

try {
    const [install, root, database, mode, symbol, file, limit, depth] = process.argv.slice(2);
    if (mode !== "callers" && mode !== "callees" && mode !== "impact") process.exit(1);
    process.stdout.write(JSON.stringify(trace(install, root, database, mode, symbol, file, Number(limit), Number(depth))));
} catch {
    process.exitCode = 1;
}
