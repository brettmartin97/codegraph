import { ChildProcessWithoutNullStreams, execFile, spawn } from 'child_process';
import { existsSync } from 'fs';
import * as path from 'path';
import { promisify } from 'util';
import * as vscode from 'vscode';
import { CodeGraphClient, FunctionAtResponse, FunctionRef } from './client';
import { FunctionPanelProvider } from './functionPanel';
import { CodeGraphHoverProvider } from './hover';

let serverProcess: ChildProcessWithoutNullStreams | undefined;
let output: vscode.OutputChannel;
let panelProvider: FunctionPanelProvider;
let statusBar: vscode.StatusBarItem;
const execFileAsync = promisify(execFile);

function cfg<T>(key: string, fallback: T): T {
    return vscode.workspace.getConfiguration('codegraph').get<T>(key, fallback);
}

function endpoint(): string {
    return cfg<string>('endpoint', 'http://127.0.0.1:8811').replace(/\/+$/, '');
}

function repoName(): string | null {
    const explicit = cfg<string>('repo', '') || cfg<string>('repoName', '');
    if (explicit) {
        return explicit;
    }
    const folder = vscode.workspace.workspaceFolders?.[0];
    return folder?.name ?? null;
}

function repoRoot(): string | null {
    return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? null;
}

function client(): CodeGraphClient | null {
    const repo = repoName();
    return repo ? new CodeGraphClient(endpoint(), repo) : null;
}

function setStatus(text: string, tooltip?: string): void {
    statusBar.text = text;
    statusBar.tooltip = tooltip;
    statusBar.show();
}

function serverArgs(): string[] {
    const url = new URL(endpoint());
    return [
        '-m',
        'uvicorn',
        'codegraph_mcp.server.rest_api:api',
        '--host',
        url.hostname || '127.0.0.1',
        '--port',
        url.port || '8811',
    ];
}

async function findPython(): Promise<string | null> {
    const configured = cfg<string>('pythonPath', '').trim();
    const root = repoRoot();
    const workspaceCandidates = root
        ? [
              path.join(root, '.venv', 'Scripts', 'python.exe'),
              path.join(root, '.venv', 'bin', 'python'),
              path.join(root, 'venv', 'Scripts', 'python.exe'),
              path.join(root, 'venv', 'bin', 'python'),
          ].filter(existsSync)
        : [];
    const candidates = configured ? [configured] : [...workspaceCandidates, 'python3', 'python', 'py'];
    for (const python of candidates) {
        try {
            await execFileAsync(
                python,
                ['-c', 'import codegraph_mcp, uvicorn'],
                {
                    timeout: 5000,
                    env: {
                        ...process.env,
                        PYTHONUTF8: '1',
                        PYTHONIOENCODING: 'utf-8',
                    },
                }
            );
            return python;
        } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            output.appendLine(`Python probe failed for ${python}: ${message}`);
        }
    }
    return null;
}

async function sleep(ms: number): Promise<void> {
    await new Promise(resolve => setTimeout(resolve, ms));
}

async function waitForServer(cg: CodeGraphClient, attempts = 30): Promise<boolean> {
    for (let i = 0; i < attempts; i++) {
        if (await cg.healthz()) {
            return true;
        }
        await sleep(500);
    }
    return false;
}

async function ensureServer(): Promise<boolean> {
    const cg = client();
    if (!cg) {
        panelProvider.setMessage('Open a workspace folder to use CodeGraph.');
        return false;
    }
    if (await cg.healthz()) {
        setStatus('$(graph) CodeGraph');
        return true;
    }
    if (!cfg<boolean>('autoStartServer', true)) {
        panelProvider.setOffline(endpoint());
        setStatus('$(warning) CodeGraph offline', endpoint());
        return false;
    }

    const python = await findPython();
    if (!python) {
        panelProvider.setMessage('CodeGraph Python environment not found. Install codegraph-mcp or set codegraph.pythonPath.');
        setStatus('$(error) CodeGraph Python missing');
        vscode.window.showErrorMessage('CodeGraph could not find Python with codegraph-mcp and uvicorn installed. Set codegraph.pythonPath or install codegraph-mcp[full].');
        return false;
    }
    const root = repoRoot() ?? undefined;
    const args = serverArgs();
    output.appendLine(`Starting CodeGraph server: ${python} ${args.join(' ')}`);
    setStatus('$(sync~spin) CodeGraph starting');

    serverProcess = spawn(python, args, {
        cwd: root,
        env: {
            ...process.env,
            CODEGRAPH_ALLOW_EXTERNAL_REPOS: 'true',
            PYTHONUTF8: '1',
            PYTHONIOENCODING: 'utf-8',
        },
    });
    serverProcess.stdout.on('data', chunk => output.append(chunk.toString()));
    serverProcess.stderr.on('data', chunk => output.append(chunk.toString()));
    serverProcess.on('exit', code => {
        output.appendLine(`CodeGraph server exited with code ${code ?? 'unknown'}`);
        if (serverProcess) {
            setStatus('$(warning) CodeGraph stopped');
        }
        serverProcess = undefined;
    });

    const ready = await waitForServer(cg);
    if (!ready) {
        panelProvider.setOffline(endpoint());
        setStatus('$(error) CodeGraph failed');
        vscode.window.showErrorMessage('CodeGraph server did not start. Run "CodeGraph: Run Doctor" for details.');
        return false;
    }
    setStatus('$(graph) CodeGraph');
    return true;
}

async function ensureWorkspaceIndexed(forceFull = false): Promise<boolean> {
    const root = repoRoot();
    const repo = repoName();
    const cg = client();
    if (!root || !repo || !cg) {
        panelProvider.setMessage('Open a workspace folder to index with CodeGraph.');
        return false;
    }
    if (!(await ensureServer())) {
        return false;
    }

    setStatus('$(sync~spin) CodeGraph indexing', root);
    panelProvider.setMessage(`Indexing ${repo}...`);
    try {
        const repos = await cg.listRepos();
        const alreadyRegistered = repos.some(r => r.name === repo);
        if (!alreadyRegistered) {
            await cg.addRepo(repo, root);
        }
        await cg.indexRepo(repo, forceFull || !alreadyRegistered ? 'full' : 'incremental');
        panelProvider.setMessage(`Ready: ${repo}`);
        setStatus('$(graph) CodeGraph ready', `${repo} indexed`);
        return true;
    } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        output.appendLine(`Index failed: ${message}`);
        panelProvider.setMessage(`Index failed: ${message}`);
        setStatus('$(error) CodeGraph index failed');
        vscode.window.showErrorMessage(`CodeGraph index failed: ${message}`);
        return false;
    }
}

function relativeFile(document: vscode.TextDocument): string | null {
    const root = repoRoot();
    if (!root) {
        return null;
    }
    const rel = path.relative(root, document.uri.fsPath).split(path.sep).join('/');
    if (rel.startsWith('..') || path.isAbsolute(rel)) {
        return null;
    }
    return rel;
}

async function functionAtCursor(): Promise<FunctionAtResponse | null> {
    const editor = vscode.window.activeTextEditor;
    const cg = client();
    if (!editor || !cg) {
        return null;
    }
    const rel = relativeFile(editor.document);
    if (!rel) {
        return null;
    }
    return cg.functionAt(rel, editor.selection.active.line + 1);
}

async function showImpactForCurrent(): Promise<void> {
    if (!(await ensureServer())) {
        return;
    }
    const cg = client();
    if (!cg) {
        return;
    }
    let fn: FunctionAtResponse | null = null;
    try {
        fn = await functionAtCursor();
    } catch {
        fn = null;
    }
    if (!fn) {
        vscode.window.showInformationMessage('CodeGraph: no indexed function at the cursor. Try saving the file or running CodeGraph: Index Workspace Repo.');
        return;
    }

    const impact = await cg.impact(fn.function.qualified_name, cfg<number>('blastRadiusDepth', 2), 8000);
    panelProvider.setFunction(fn, impact?.risk_score, impact?.risk_level, impact?.reasons);
}

async function openFunction(ref: FunctionRef): Promise<void> {
    const root = repoRoot();
    if (!root) {
        return;
    }
    const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(path.join(root, ref.file)));
    const editor = await vscode.window.showTextDocument(doc);
    const pos = new vscode.Position(Math.max(0, ref.line - 1), 0);
    editor.selection = new vscode.Selection(pos, pos);
    editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
}

async function refreshActive(): Promise<void> {
    if (!(await ensureServer())) {
        return;
    }
    const current = await functionAtCursor();
    if (current) {
        panelProvider.setFunction(current);
    } else {
        panelProvider.setMessage('Ready. Hover a function or run Show Impact at the cursor.');
    }
}

export function activate(context: vscode.ExtensionContext): void {
    output = vscode.window.createOutputChannel('CodeGraph');
    panelProvider = new FunctionPanelProvider();
    statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 80);
    statusBar.command = 'codegraph.refresh';
    context.subscriptions.push(output, statusBar);

    context.subscriptions.push(
        vscode.window.createTreeView('codegraph.functions', {
            treeDataProvider: panelProvider,
            showCollapseAll: true,
        })
    );

    context.subscriptions.push(
        vscode.languages.registerHoverProvider(
            { scheme: 'file' },
            new CodeGraphHoverProvider(client, repoRoot, resp => panelProvider.setFunction(resp))
        )
    );

    context.subscriptions.push(
        vscode.commands.registerCommand('codegraph.indexWorkspace', () => ensureWorkspaceIndexed(true)),
        vscode.commands.registerCommand('codegraph.refresh', refreshActive),
        vscode.commands.registerCommand('codegraph.showImpactForCurrent', showImpactForCurrent),
        vscode.commands.registerCommand('codegraph.openFunction', openFunction),
        vscode.commands.registerCommand('codegraph.runDoctor', () => {
            const terminal = vscode.window.createTerminal('CodeGraph Doctor');
            terminal.show();
            terminal.sendText(`${cfg<string>('pythonPath', '').trim() || 'python'} -m codegraph_mcp.cli.app doctor`);
        })
    );

    setStatus('$(sync~spin) CodeGraph starting');
    panelProvider.setMessage('Starting CodeGraph...');
    void (async () => {
        const online = await ensureServer();
        if (online && cfg<boolean>('autoIndexOnStart', true)) {
            await ensureWorkspaceIndexed(false);
        } else if (online) {
            panelProvider.setMessage('Ready. Run CodeGraph: Index Workspace Repo if this repo is not indexed.');
        }
    })();
}

export function deactivate(): void {
    if (serverProcess) {
        const proc = serverProcess;
        serverProcess = undefined;
        proc.kill();
    }
    statusBar?.dispose();
}
