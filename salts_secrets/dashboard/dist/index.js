(function () {
  "use strict";
  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;

  const React = SDK.React;
  const { useState, useEffect, useCallback } = React;
  const h = React.createElement;
  const API = "/api/plugins/salts-secrets";

  const get = (path) => SDK.fetchJSON(API + path);
  const post = (path, body) => SDK.fetchJSON(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });

  function useLoad(path) {
    const [state, setState] = useState({ data: null, error: null });
    const reload = useCallback(() => {
      get(path).then((data) => setState({ data, error: null }), (error) => setState({ data: null, error }));
    }, [path]);
    useEffect(reload, [reload]);
    return [state, reload];
  }

  function Status({ state, children }) {
    if (state.error) return h("p", { className: "ss-error" }, String(state.error.message || state.error));
    if (!state.data) return h("p", { className: "ss-muted" }, "Loading…");
    return children(state.data);
  }

  function ContextTab() {
    const [state, reload] = useLoad("/items");
    const [picked, setPicked] = useState(null);
    const [texts, setTexts] = useState({});
    const [open, setOpen] = useState(null);
    const [result, setResult] = useState(null);

    useEffect(() => {
      if (!state.data) return;
      setPicked(new Set(state.data.items.filter((i) => i.status === "public").map((i) => i.id)));
      setTexts(Object.fromEntries(state.data.items.filter((i) => i.public_text).map((i) => [i.id, i.public_text])));
    }, [state.data]);

    const toggle = (id) => setPicked((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

    const show = (item) => {
      if (open && open.id === item.id) return setOpen(null);
      get("/items/" + encodeURIComponent(item.id)).then((r) => setOpen({ id: item.id, text: r.text }));
    };

    const publish = () => post("/publish", { public_ids: [...picked], public_texts: texts })
      .then((r) => { setResult(r); reload(); }, (e) => setResult({ error: String(e.message || e) }));

    return h(Status, { state }, (data) => {
      const groups = {};
      data.items.forEach((item) => (groups[item.group] = groups[item.group] || []).push(item));
      return h("div", null,
        h("p", { className: "ss-lead" },
          "Everything below is private by default. Tick what strangers may know about ", data.owner_name,
          "; the public profile only ever sees the ticked items."),
        Object.entries(groups).map(([group, items]) => h("section", { key: group, className: "ss-group" },
          h("h3", null, group),
          items.map((item) => h("div", { key: item.id, className: "ss-item ss-" + item.status },
            h("label", null,
              h("input", {
                type: "checkbox", disabled: !item.publishable,
                checked: !!(picked && picked.has(item.id)), onChange: () => toggle(item.id),
              }),
              h("span", { className: "ss-title" }, item.title),
              h("span", { className: "ss-badge" }, item.publishable ? item.status : "never shared")),
            h("button", { className: "ss-link", onClick: () => show(item) }, open && open.id === item.id ? "hide" : "view"),
            open && open.id === item.id && h("pre", { className: "ss-text" }, open.text),
            picked && picked.has(item.id) && h("textarea", {
              className: "ss-rewrite", placeholder: "Optional: what guests see instead of the original",
              value: texts[item.id] || "", onChange: (e) => setTexts({ ...texts, [item.id]: e.target.value }),
            }))))),
        h("div", { className: "ss-actions" },
          h("button", { className: "ss-primary", onClick: publish, disabled: !picked }, "Publish to guests"),
          result && (result.error
            ? h("span", { className: "ss-error" }, result.error)
            : h("span", { className: "ss-muted" },
              `${result.facts} facts and ${result.documents} documents public` +
              (result.revoked ? `; ${result.revoked} revoked, ${result.deleted} guest sessions purged` : "")))));
    });
  }

  function PermissionsTab() {
    const [state, reload] = useLoad("/permissions");
    const [guest, setGuest] = useState(null);
    useEffect(() => { if (state.data) setGuest(new Set(state.data.guest)); }, [state.data]);
    const toggle = (id) => setGuest((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
    return h(Status, { state }, (data) => h("div", null,
      h("p", { className: "ss-lead" },
        "Toolsets the public profile may use. Locked toolsets touch your machine or memory and are never offered to guests."),
      h("table", { className: "ss-table" }, h("tbody", null, data.catalog.map((t) => h("tr", { key: t.id, className: t.locked ? "ss-locked" : "" },
        h("td", null, h("input", {
          type: "checkbox", disabled: t.locked, checked: !!(guest && guest.has(t.id)), onChange: () => toggle(t.id),
        })),
        h("td", null, h("strong", null, t.label), h("div", { className: "ss-muted" }, t.tools.join(", "))),
        h("td", { className: "ss-muted" }, t.locked_reason || t.warning || ""))))),
      h("div", { className: "ss-actions" },
        h("button", { className: "ss-primary", disabled: !guest, onClick: () => post("/permissions", { guest: [...guest] }).then(reload) },
          "Save"))));
  }

  function GuestsTab() {
    const [state, reload] = useLoad("/guests");
    return h(Status, { state }, (data) => h("div", null,
      h("p", { className: "ss-lead" },
        `${data.counts.sessions} guest sessions, ${data.counts.questions} questions, ${data.counts.probes} looked like probes.`),
      data.sessions.length === 0 && h("p", { className: "ss-muted" }, "No guest has talked to the public profile yet."),
      data.sessions.map((s) => h("section", { key: s.id, className: "ss-group" },
        h("h3", null, new Date((s.started_at || 0) * 1000).toLocaleString(), " · ", s.source || "api", s.live ? "" : " · archived"),
        s.turns.map((turn, i) => h("div", { key: i, className: "ss-turn ss-" + turn.role },
          h("strong", null, turn.role === "user" ? "guest" : "public profile"), " ", turn.text)))),
      h("div", { className: "ss-actions" },
        h("button", { onClick: () => post("/guests/clear").then(reload) }, "Archive and clear guest sessions"))));
  }

  function CheckTab() {
    const [state, reload] = useLoad("/check");
    return h(Status, { state }, (data) => h("div", null,
      h("p", { className: data.ok ? "ss-ok ss-headline" : "ss-error ss-headline" }, data.line),
      h("ul", { className: "ss-checks" }, data.checks.map((c, i) => h("li", { key: i, className: c.ok ? "ss-ok" : "ss-error" },
        (c.ok ? "✓ " : "✗ ") + c.text))),
      h("div", { className: "ss-actions" }, h("button", { onClick: reload }, "Run again"))));
  }

  const TABS = [["Context", ContextTab], ["Permissions", PermissionsTab], ["Guests", GuestsTab], ["Check", CheckTab]];

  function Page() {
    const [tab, setTab] = useState(0);
    return h("div", { className: "ss-page" },
      h("header", null,
        h("h1", null, "Salt's Secrets"),
        h("p", { className: "ss-muted" }, "One Hermes, two minds: your DM reaches you, everyone else reaches the public profile.")),
      h("nav", { className: "ss-tabs" }, TABS.map(([label], i) =>
        h("button", { key: label, className: i === tab ? "ss-tab ss-active" : "ss-tab", onClick: () => setTab(i) }, label))),
      h(TABS[tab][1]));
  }

  window.__HERMES_PLUGINS__.register("salts-secrets", Page);
})();
