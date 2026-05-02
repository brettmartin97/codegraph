/**
 * Thin client for the CodeGraph REST API.
 *
 * Only depends on the Node `fetch` API (Node ≥ 18, which VS Code ≥ 1.85 ships).
 * Surface mirrors the endpoints exposed by `codegraph_mcp.server.rest_api`.
 */

export interface FunctionSummary {
    qualified_name: string;
    name: string;
    kind: string;
    file: string;
    start_line: number;
    end_line: number;
    loc: number;
    signature: string | null;
    parameters: { name: string; type_hint: string | null; default: string | null; position: number; keyword_only?: boolean }[];
    return_type: string | null;
    decorators: string[];
    enclosing_class: string | null;
    is_async: boolean;
    is_test: boolean;
    complexity: number | null;
    summary: string | null;
    purpose: string | null;
    category: string | null;
    docstring: string | null;
    side_effects: string[];
}

export interface FunctionRef {
    qualified_name: string;
    file: string;
    line: number;
}

export interface LastChange {
    commit: string;
    author: string;
    date: string;
    subject: string;
    days_ago: number | null;
    human: string | null;
}

export interface FunctionAtResponse {
    repo: string;
    function: FunctionSummary;
    stats: { caller_count: number; callee_count: number; test_count: number };
    callers: FunctionRef[];
    callees: string[];
    tests: FunctionRef[];
    last_change: LastChange | null;
}

export interface ImpactReport {
    target_function: { qualified_name: string };
    direct_callers: { qualified_name: string }[];
    transitive_callers: { qualified_name: string }[];
    direct_callees: { qualified_name: string }[];
    related_tests: { qualified_name: string }[];
    risk_score: number;
    risk_level: string;
    reasons: string[];
    recommended_validation: string[];
}

export class CodeGraphClient {
    constructor(private readonly endpoint: string, private readonly repo: string) {}

    private url(path: string): string {
        const base = this.endpoint.replace(/\/+$/, '');
        return `${base}${path}`;
    }

    private async requireOk(res: Response, action: string): Promise<void> {
        if (res.ok) {
            return;
        }
        let detail = `${res.status} ${res.statusText}`;
        try {
            const body = await res.json() as { detail?: string };
            if (body.detail) {
                detail = body.detail;
            }
        } catch {
            // Keep the status text if the response is not JSON.
        }
        throw new Error(`${action} failed: ${detail}`);
    }

    async healthz(): Promise<boolean> {
        try {
            const res = await fetch(this.url('/healthz'));
            return res.ok;
        } catch {
            return false;
        }
    }

    async listRepos(): Promise<{ name: string; path: string }[]> {
        const res = await fetch(this.url('/repos'));
        if (!res.ok) {
            return [];
        }
        return (await res.json()) as { name: string; path: string }[];
    }

    async addRepo(name: string, path: string): Promise<void> {
        const res = await fetch(this.url('/repos'), {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ name, path }),
        });
        await this.requireOk(res, 'Register repo');
    }

    async indexRepo(name: string, mode: 'full' | 'incremental' = 'full'): Promise<unknown> {
        const res = await fetch(this.url(`/repos/${encodeURIComponent(name)}/index`), {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ mode }),
        });
        await this.requireOk(res, 'Index repo');
        return res.json();
    }

    async functionAt(file: string, line: number): Promise<FunctionAtResponse | null> {
        const params = new URLSearchParams({ file, line: String(line) });
        const res = await fetch(
            this.url(`/repos/${encodeURIComponent(this.repo)}/function-at?${params.toString()}`)
        );
        if (res.status === 404) {
            return null;
        }
        if (!res.ok) {
            return null;
        }
        return (await res.json()) as FunctionAtResponse;
    }

    async impact(qualifiedName: string, depth = 2, maxTokens = 4000): Promise<ImpactReport | null> {
        const res = await fetch(
            this.url(`/repos/${encodeURIComponent(this.repo)}/impact`),
            {
                method: 'POST',
                headers: { 'content-type': 'application/json' },
                body: JSON.stringify({ function: qualifiedName, depth, max_tokens: maxTokens }),
            }
        );
        if (!res.ok) {
            return null;
        }
        return (await res.json()) as ImpactReport;
    }
}
