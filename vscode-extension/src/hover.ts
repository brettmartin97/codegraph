import * as path from 'path';
import * as vscode from 'vscode';
import { CodeGraphClient, FunctionAtResponse } from './client';

/**
 * Hover provider that asks CodeGraph: "what function is at file:line, and what
 * does the call graph say about it?". Returns a markdown card with the
 * headline stats plus quick links into the sidebar.
 */
export class CodeGraphHoverProvider implements vscode.HoverProvider {
    constructor(
        private readonly client: () => CodeGraphClient | null,
        private readonly repoRoot: () => string | null,
        private readonly onResolved: (resp: FunctionAtResponse) => void
    ) {}

    async provideHover(
        document: vscode.TextDocument,
        position: vscode.Position,
        _token: vscode.CancellationToken
    ): Promise<vscode.Hover | null> {
        const enabled = vscode.workspace
            .getConfiguration('codegraph')
            .get<boolean>('hover.enabled', true);
        if (!enabled) {
            return null;
        }
        const client = this.client();
        const root = this.repoRoot();
        if (!client || !root) {
            return null;
        }
        // Only show the hover when the cursor is on an identifier — avoids
        // popping the card over whitespace, comments, or punctuation.
        const wordRange = document.getWordRangeAtPosition(position, /[A-Za-z_][A-Za-z0-9_]*/);
        if (!wordRange) {
            return null;
        }

        const filePath = document.uri.fsPath;
        const rel = path.relative(root, filePath).split(path.sep).join('/');
        if (rel.startsWith('..')) {
            return null;
        }

        const line = position.line + 1; // CodeGraph uses 1-based line numbers.
        let resp: FunctionAtResponse | null = null;
        try {
            resp = await client.functionAt(rel, line);
        } catch {
            return null;
        }
        if (!resp) {
            return null;
        }
        this.onResolved(resp);

        return new vscode.Hover(buildHoverMarkdown(resp, document.languageId), wordRange);
    }
}

function buildHoverMarkdown(r: FunctionAtResponse, languageId: string): vscode.MarkdownString {
    const md = new vscode.MarkdownString('', true);
    md.isTrusted = true;
    md.supportHtml = false;

    const fn = r.function;
    const stats = r.stats;
    const lc = r.last_change;

    // ── Title ────────────────────────────────────────────────────────────────
    const kindBadge = fn.kind && fn.kind !== 'function' ? ` _(${fn.kind})_` : '';
    const classBadge = fn.enclosing_class ? ` in \`${fn.enclosing_class}\`` : '';
    md.appendMarkdown(`### \`${fn.qualified_name}\`${kindBadge}${classBadge}\n`);

    // ── Signature ────────────────────────────────────────────────────────────
    if (fn.signature) {
        md.appendCodeblock(fn.signature, languageId);
    }

    // ── Summary / docstring ──────────────────────────────────────────────────
    const description = fn.purpose ?? fn.summary;
    if (description) {
        md.appendMarkdown(`\n${description}\n`);
    }

    // ── Headline stats ───────────────────────────────────────────────────────
    const headline: string[] = [];
    headline.push(`📞 **${stats.caller_count}** caller${stats.caller_count === 1 ? '' : 's'}`);
    headline.push(`🔻 **${stats.callee_count}** call${stats.callee_count === 1 ? '' : 's'} out`);
    headline.push(`🧪 **${stats.test_count}** test${stats.test_count === 1 ? '' : 's'}`);
    if (lc?.human) {
        headline.push(`🕒 **${lc.human}**`);
    }
    md.appendMarkdown(`\n${headline.join(' · ')}\n`);

    // ── Meta row ─────────────────────────────────────────────────────────────
    const meta: string[] = [];
    if (fn.category) { meta.push(`\`${fn.category}\``); }
    if (fn.complexity != null) { meta.push(`complexity ${fn.complexity}`); }
    if (fn.loc) { meta.push(`${fn.loc} loc`); }
    if (fn.is_async) { meta.push('async'); }
    if (fn.is_test) { meta.push('test'); }
    if (meta.length > 0) {
        md.appendMarkdown(`\n_${meta.join(' · ')}_\n`);
    }

    // ── Parameters ───────────────────────────────────────────────────────────
    if (fn.parameters && fn.parameters.length > 0) {
        const paramLines = fn.parameters
            .filter(p => p.name !== 'self' && p.name !== 'cls')
            .map(p => {
                let s = `\`${p.name}\``;
                if (p.type_hint) { s += `: ${p.type_hint}`; }
                if (p.default !== null && p.default !== undefined) { s += ` = ${p.default}`; }
                return s;
            });
        if (paramLines.length > 0) {
            md.appendMarkdown(`\n**Params** — ${paramLines.join(', ')}\n`);
        }
    }

    // ── Return type ──────────────────────────────────────────────────────────
    if (fn.return_type && fn.return_type !== 'None') {
        md.appendMarkdown(`\n**Returns** \`${fn.return_type}\`\n`);
    }

    // ── Decorators ───────────────────────────────────────────────────────────
    if (fn.decorators && fn.decorators.length > 0) {
        md.appendMarkdown(`\n**Decorators** ${fn.decorators.map(d => `\`@${d.replace(/^@/, '')}\``).join(', ')}\n`);
    }

    // ── Side effects ─────────────────────────────────────────────────────────
    if (fn.side_effects && fn.side_effects.length > 0) {
        md.appendMarkdown(`\n⚠️ _${fn.side_effects.join(', ')}_\n`);
    }

    // ── Last commit ──────────────────────────────────────────────────────────
    if (lc?.subject) {
        md.appendMarkdown(`\n> **${lc.author}** — ${lc.subject}\n`);
    }

    // ── Callers ──────────────────────────────────────────────────────────────
    if (r.callers.length > 0) {
        md.appendMarkdown(`\n**Called by**\n`);
        for (const c of r.callers.slice(0, 5)) {
            md.appendMarkdown(`- \`${c.qualified_name}\`\n`);
        }
    }

    // ── Callees ──────────────────────────────────────────────────────────────
    if (r.callees && r.callees.length > 0) {
        md.appendMarkdown(`\n**Calls**\n`);
        for (const c of r.callees.slice(0, 8)) {
            md.appendMarkdown(`- \`${c}\`\n`);
        }
    }

    // ── Tests ────────────────────────────────────────────────────────────────
    if (r.tests.length > 0) {
        md.appendMarkdown(`\n**Tests covering this**\n`);
        for (const t of r.tests.slice(0, 5)) {
            md.appendMarkdown(`- \`${t.qualified_name}\`\n`);
        }
    }

    md.appendMarkdown(
        `\n[Open full impact in sidebar](command:codegraph.showImpactForCurrent)`
    );
    return md;
}
