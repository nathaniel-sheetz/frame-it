// The batch lives in the browser script. This loads that script with a stand-in
// page, so the cases below run the real functions.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const root = new URL('..', import.meta.url);

function element(id) {
    return {
        id: id || '',
        hidden: true,
        className: '',
        textContent: '',
        value: '',
        checked: false,
        disabled: false,
        src: '',
        onload: null,
        type: '',
        title: '',
        placeholder: '',
        required: false,
        accept: '',
        options: [{ value: '' }],
        selectedOptions: [{ value: '', textContent: '' }],
        children: [],
        style: {},
        classList: { add() {}, remove() {}, toggle() {} },
        append(...nodes) { this.children.push(...nodes); },
        replaceChildren(...nodes) { this.children = nodes; },
        addEventListener() {},
        setAttribute() {},
        removeAttribute(name) { if (name === 'src') this.src = ''; },
        scrollIntoView() {},
        click() {},
        close() {},
        querySelector() { return element(); },
        querySelectorAll() { return []; },
    };
}

function page() {
    const elements = new Map();
    const store = new Map();
    const location = {
        pathname: '/',
        search: '',
        hash: '',
        origin: 'http://localhost',
        href: 'http://localhost/',
    };
    const document = {
        getElementById(id) {
            if (!elements.has(id)) elements.set(id, element(id));
            return elements.get(id);
        },
        createElement() { return element(); },
        querySelector() { return element(); },
        querySelectorAll() { return []; },
        addEventListener() {},
    };
    const context = {
        console,
        URL,
        URLSearchParams,
        Map,
        Set,
        document,
        location,
        history: {
            replaceState(_state, _title, url) {
                const next = new URL(url, location.origin);
                location.pathname = next.pathname;
                location.search = next.search;
                location.hash = next.hash;
                location.href = next.href;
            },
        },
        sessionStorage: {
            getItem: key => (store.has(key) ? store.get(key) : null),
            setItem: (key, value) => store.set(key, String(value)),
            clear: () => store.clear(),
        },
        addEventListener() {},
        Option: function Option(text, value) {
            return { text, value, textContent: text };
        },
        fetch: async () => { throw new Error('unexpected fetch'); },
    };
    context.window = context;
    context.globalThis = context;
    vm.createContext(context);
    return { context, document, location, store };
}

function load(context, source, filename) {
    vm.runInContext(source, context, { filename });
}

function json(body) {
    return { ok: true, status: 200, async json() { return body; } };
}

const a = 'a'.repeat(32);
const b = 'b'.repeat(32);

function editor() {
    const loaded = page();
    const source = fs.readFileSync(new URL('app/static/editor.js', root), 'utf8') + `
        globalThis.__editor = {
            batch,
            get opening() { return opening; },
            set opening(value) { opening = value; },
            finished,
            start,
        };
    `;
    load(loaded.context, source, 'app/static/editor.js');
    return loaded;
}

// A send that finishes after the picture was left marks that picture and stays put.
{
    const { context, document } = editor();
    const { batch, finished } = context.__editor;
    batch.ids = [a, b];
    batch.at = 1;
    context.__editor.opening = 2;
    finished('Sent to the TV.', 'success', {
        url: '/result/x', opening: 1, id: a, name: 'One', partnerId: null,
    });
    assert.equal(batch.marks.get(a), 'done');
    assert.equal(batch.marks.has(b), false);
    assert.equal(batch.at, 1);
    assert.equal(context.__editor.opening, 2);
    assert.equal(document.getElementById('send-status').textContent, '');
    assert.equal(document.getElementById('queue-done').hidden, true);
}

// A partner counts as made, and a picture already sent stays marked done.
{
    const { context, document } = editor();
    const { batch, finished } = context.__editor;
    batch.ids = [a, b];
    batch.at = 0;
    context.__editor.opening = 1;
    batch.marks.set(b, 'done');
    finished('Saved.', 'success', {
        url: '/result/x', opening: 1, id: a, name: 'One', partnerId: b,
    });
    assert.equal(batch.marks.get(a), 'done');
    assert.equal(batch.marks.get(b), 'done');
    assert.equal(document.getElementById('queue-position').textContent, 'All done: 2 made, 0 skipped');

    const again = editor();
    const second = again.context.__editor;
    second.batch.ids = [a, b];
    second.batch.at = 0;
    second.opening = 1;
    second.finished('Saved.', 'success', {
        url: '/result/x', opening: 1, id: a, name: 'One', partnerId: b,
    });
    assert.equal(second.batch.marks.get(b), 'partner');
    assert.equal(again.document.getElementById('queue-position').textContent, 'All done: 2 made, 0 skipped');
}

// Reloading a finished batch stays on the done screen.
{
    const { context, document, location, store } = editor();
    location.pathname = '/editor';
    location.search = `?ids=${a},${b}`;
    location.hash = '';
    location.href = `http://localhost/editor?ids=${a},${b}`;
    store.set(`editor-marks:${a},${b}`, JSON.stringify({ [a]: 'done', [b]: 'done' }));
    const fetched = [];
    context.fetch = async (url) => {
        fetched.push(String(url));
        if (url === '/api/library') {
            return json({
                items: [
                    { id: a, name: 'One', file: `${a}.jpg`, kind: 'original' },
                    { id: b, name: 'Two', file: `${b}.jpg`, kind: 'original' },
                ],
            });
        }
        if (url === '/api/playlists') return json({ items: [] });
        throw new Error(`unexpected fetch ${url}`);
    };
    await context.__editor.start();
    assert.equal(context.__editor.batch.at, -1);
    assert.equal(document.getElementById('queue-done').hidden, false);
    assert.equal(document.getElementById('queue-position').textContent, 'All done: 2 made, 0 skipped');
    assert.equal(fetched.some(url => url.startsWith('/api/library/')), false);
}

// Choosing more than 100 pictures stays on the library and says so.
{
    const html = fs.readFileSync(new URL('app/static/library.html', root), 'utf8');
    const source = html.match(/<script>([\s\S]*?)<\/script>/)[1] + `
        globalThis.__library = { chosen, msg };
    `;
    const { context, document, location } = page();
    location.pathname = '/library';
    location.href = 'http://localhost/library';
    context.fetch = async (url) => {
        if (url === '/api/library') return json({ items: [], count: 0, bytes: 0 });
        if (url === '/api/art') return json({});
        throw new Error(`unexpected fetch ${url}`);
    };
    load(context, source, 'app/static/library.html');
    for (let i = 0; i < 30; i++) await new Promise(resolve => setImmediate(resolve));

    const { chosen } = context.__library;
    for (let i = 0; i < 101; i++) chosen.add(i.toString(16).padStart(32, '0'));
    document.getElementById('edit-chosen').onclick();
    assert.equal(location.href, 'http://localhost/library');
    assert.equal(context.__library.msg.textContent, '100 is the most that can be edited at once. Deselect some first.');
    assert.equal(context.__library.msg.className, 'err');

    chosen.clear();
    for (let i = 0; i < 100; i++) chosen.add(i.toString(16).padStart(32, '0'));
    document.getElementById('edit-chosen').onclick();
    const opened = new URL(location.href, 'http://localhost');
    assert.equal(opened.pathname, '/editor');
    assert.equal(opened.searchParams.get('ids').split(',').length, 100);
}

console.log('editor page checks passed');
