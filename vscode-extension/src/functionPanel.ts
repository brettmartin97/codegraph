import * as vscode from 'vscode';
import { FunctionAtResponse, FunctionRef } from './client';

type NodeKind = 'header' | 'section' | 'ref' | 'message' | 'reason';

export class FunctionPanelNode extends vscode.TreeItem {
    constructor(
        label: string,
        public readonly kind: NodeKind,
        public readonly children: FunctionPanelNode[] = [],
        public readonly ref?: FunctionRef
    ) {
        super(
            label,
            children.length > 0
                ? vscode.TreeItemCollapsibleState.Expanded
                : vscode.TreeItemCollapsibleState.None
        );
        if (kind === 'header') {
            this.iconPath = new vscode.ThemeIcon('symbol-function');
        } else if (kind === 'section') {
            this.iconPath = new vscode.ThemeIcon('list-tree');
        } else if (kind === 'ref') {
            this.iconPath = new vscode.ThemeIcon('arrow-right');
            if (ref) {
                this.description = `${ref.file}:${ref.line}`;
                this.tooltip = `${ref.qualified_name}\n${ref.file}:${ref.line}`;
                this.command = {
                    command: 'codegraph.openFunction',
                    title: 'Open',
                    arguments: [ref],
                };
            }
        } else if (kind === 'reason') {
            this.iconPath = new vscode.ThemeIcon('info');
        }
    }
}

export class FunctionPanelProvider implements vscode.TreeDataProvider<FunctionPanelNode> {
    private _onDidChange = new vscode.EventEmitter<void>();
    readonly onDidChangeTreeData = this._onDidChange.event;

    private root: FunctionPanelNode[] = [];

    setMessage(message: string): void {
        this.root = [new FunctionPanelNode(message, 'message')];
        this._onDidChange.fire();
    }

    setOffline(endpoint: string): void {
        this.root = [
            new FunctionPanelNode(`REST API offline: ${endpoint}`, 'message'),
            new FunctionPanelNode('The VS Code MCP server can be running while this REST API is offline.', 'reason'),
            new FunctionPanelNode('1. Start the REST API:', 'message'),
            new FunctionPanelNode('   python -m uvicorn codegraph_mcp.server.rest_api:api --port 8811', 'reason'),
            new FunctionPanelNode('2. Register + index a repo:', 'message'),
            new FunctionPanelNode('   codegraph repo add <name> <path>', 'reason'),
            new FunctionPanelNode('   codegraph index <name>', 'reason'),
            new FunctionPanelNode('Then run: Ctrl+Shift+P -> CodeGraph: Refresh', 'message'),
        ];
        this._onDidChange.fire();
    }

    setFunction(resp: FunctionAtResponse, riskScore?: number, riskLevel?: string,
                reasons?: string[]): void {
        const fn = resp.function;
        const stats = resp.stats;
        const lc = resp.last_change;

        const header = new FunctionPanelNode(fn.qualified_name, 'header');
        header.description = fn.signature ?? '';
        header.tooltip = new vscode.MarkdownString(
            `**${fn.qualified_name}**\n\n` +
                (fn.purpose ?? fn.summary ?? '_no description_') +
                `\n\n${fn.file}:${fn.start_line}-${fn.end_line}`
        );

        const summarize = (label: string, count: number): FunctionPanelNode => {
            const n = new FunctionPanelNode(`${label}: ${count}`, 'section');
            return n;
        };

        const headline = new FunctionPanelNode('Summary', 'section', [
            summarize('Callers', stats.caller_count),
            summarize('Callees', stats.callee_count),
            summarize('Tests covering this', stats.test_count),
            ...(lc?.human
                ? [new FunctionPanelNode(`Last changed: ${lc.human}`, 'section')]
                : []),
            ...(riskScore != null
                ? [
                      new FunctionPanelNode(
                          `Risk: ${riskScore.toFixed(2)} (${riskLevel ?? 'unknown'})`,
                          'section'
                      ),
                  ]
                : []),
        ]);

        const callers = new FunctionPanelNode(
            `Callers (${resp.callers.length})`,
            'section',
            resp.callers.map(
                (c) => new FunctionPanelNode(c.qualified_name, 'ref', [], c)
            )
        );
        const tests = new FunctionPanelNode(
            `Tests (${resp.tests.length})`,
            'section',
            resp.tests.map(
                (t) => new FunctionPanelNode(t.qualified_name, 'ref', [], t)
            )
        );

        const sections: FunctionPanelNode[] = [header, headline, callers, tests];

        if (lc?.subject) {
            sections.push(
                new FunctionPanelNode('Last commit', 'section', [
                    new FunctionPanelNode(
                        `${lc.human ?? lc.date} — ${lc.author}`,
                        'reason'
                    ),
                    new FunctionPanelNode(lc.subject, 'reason'),
                    new FunctionPanelNode(`commit ${lc.commit}`, 'reason'),
                ])
            );
        }
        if (reasons && reasons.length > 0) {
            sections.push(
                new FunctionPanelNode(
                    'Risk reasons',
                    'section',
                    reasons.map((r) => new FunctionPanelNode(r, 'reason'))
                )
            );
        }

        this.root = sections;
        this._onDidChange.fire();
    }

    getTreeItem(el: FunctionPanelNode): vscode.TreeItem {
        return el;
    }

    getChildren(el?: FunctionPanelNode): FunctionPanelNode[] {
        return el ? el.children : this.root;
    }
}
