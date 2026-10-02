/*
  Конструктор бота.

  Форми будуються зі схем пайплайнів (JSON Schema з бекенду), тому новий пайплайн
  з'являється в інтерфейсі сам: підписи беруться з title у схемі, тип поля — з type.
*/

const api = {
  get: (url) => api.send(url),
  post: (url, body) => api.send(url, "POST", body || {}),
  put: (url, body) => api.send(url, "PUT", body),
  async send(url, method = "GET", body) {
    const res = await fetch(url, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  },
};

const CATEGORY = {
  pet: "Пет", combat: "Бій", loot: "Лут", nav: "Повернення", service: "Обслуговування",
  "інше": "Інше",
};
const S = {
  config: null, catalog: [], state: null,
  window: null,          // ім'я вибраного вікна (нік персонажа)
  removed: new Set(),    // персонажі, яких прибрали в інтерфейсі, але ще не зберегли
  profile: null,         // який профіль браузиться в конструкторі; null = профіль вибраного персонажа
  dirty: false, open: {}, timer: null, cardsKey: "",
  // технічні поля (зони, кольори, пороги) сховані: вони потрібні лише
  // при перекалібруванні під іншу роздільну здатність
  tech: false,
};

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

function toast(message, kind = "") {
  const item = el("div", `toast-item ${kind}`, message);
  $("toast").appendChild(item);
  setTimeout(() => item.remove(), 4200);
}

function markDirty() {
  S.dirty = true;
  const btn = $("btnSave");
  btn.disabled = false;
  btn.textContent = "Зберегти •";
  if (!S.warnedDirty) {
    S.warnedDirty = true;
    toast("є незбережені зміни — натисни «Зберегти»");
  }
}

function toggle(checked, onChange) {
  const wrap = el("label", "switch");
  const input = el("input");
  input.type = "checkbox";
  input.checked = checked;
  input.onchange = () => onChange(input.checked);
  wrap.append(input, el("span"));
  return wrap;
}

/* ---------- схеми ---------- */
function resolveRef(schema, root) {
  if (!schema) return {};
  // сусідні ключі (title, description, tech) мають перекривати те, що в $defs,
  // інакше позначка «технічне» губилась би на полях-посиланнях
  const own = { ...schema };
  delete own.$ref;
  delete own.anyOf;
  delete own.allOf;
  if (schema.$ref) {
    const name = schema.$ref.split("/").pop();
    return { ...(root.$defs || {})[name], ...own };
  }
  if (schema.allOf && schema.allOf.length === 1) {
    return { ...resolveRef(schema.allOf[0], root), ...own };
  }
  if (schema.anyOf) {                              // Optional[X] -> anyOf [X, null]
    const real = schema.anyOf.find((s) => s.type !== "null") || {};
    return { ...resolveRef(real, root), ...own, nullable: true };
  }
  return schema;
}

function inputFor(spec, value, onChange) {
  if (spec.type === "boolean") {
    return toggle(!!value, onChange);
  }
  const input = el("input");
  if (spec.type === "integer" || spec.type === "number") {
    input.type = "number";
    if (spec.type === "number") input.step = "0.1";
    if (spec.minimum !== undefined) input.min = spec.minimum;
    if (spec.maximum !== undefined) input.max = spec.maximum;
    input.value = value ?? "";
    input.onchange = () => onChange(input.value === "" ? null : Number(input.value));
    return input;
  }
  input.type = "text";
  input.value = value ?? "";
  if (spec.nullable) input.placeholder = "порожньо = вимкнено";
  input.onchange = () => onChange(input.value === "" && spec.nullable ? null : input.value);
  return input;
}

function tupleInput(spec, value, onChange) {
  const box = el("div", "tuple");
  const parts = spec.prefixItems || [];
  const current = Array.isArray(value) ? [...value] : parts.map(() => 0);
  parts.forEach((sub, i) => {
    const input = el("input");
    input.type = "number";
    input.value = current[i] ?? 0;
    input.onchange = () => { current[i] = Number(input.value); onChange([...current]); };
    box.appendChild(input);
  });
  return box;
}

function dictInput(value, onChange) {
  const box = el("div");
  const data = { ...(value || {}) };
  const redraw = () => {
    box.innerHTML = "";
    Object.entries(data).forEach(([key, num]) => {
      const row = el("div", "kv-row");
      const k = el("input"); k.value = key; k.title = "клавіша";
      const v = el("input"); v.type = "number"; v.step = "0.1"; v.value = num; v.title = "інтервал, с";
      const del = el("button", "quiet", "×"); del.title = "прибрати";
      k.onchange = () => { delete data[key]; data[k.value] = Number(v.value); onChange({ ...data }); redraw(); };
      v.onchange = () => { data[key] = Number(v.value); onChange({ ...data }); };
      del.onclick = () => { delete data[key]; onChange({ ...data }); redraw(); };
      row.append(k, v, del);
      box.appendChild(row);
    });
    const add = el("button", "quiet", "+ клавіша");
    add.onclick = () => { data[`клавіша${Object.keys(data).length + 1}`] = 1; onChange({ ...data }); redraw(); };
    box.appendChild(add);
  };
  redraw();
  return box;
}

/* Сітка рюкзака: відмічаєш комірки, які продавати. Значення — рядки «рядок:стовпець». */
function cellsInput(value, onChange, rows = 4, cols = 8) {
  const chosen = new Set(Array.isArray(value) ? value : []);
  const box = el("div", "cells");
  const selected = () => [...chosen].sort((a, b) => {
    const [ar, ac] = a.split(":").map(Number);
    const [br, bc] = b.split(":").map(Number);
    return ar - br || ac - bc;
  });
  const commit = () => onChange(selected());
  const redraw = () => {
    box.innerHTML = "";

    const header = el("div", "cells-row cells-header");
    header.appendChild(el("span", "cells-axis", "ряд"));
    for (let c = 1; c <= cols; c++) header.appendChild(el("span", "cells-col", String(c)));
    header.appendChild(el("span", "cells-row-caption", "увесь рядок"));
    box.appendChild(header);

    for (let r = 1; r <= rows; r++) {
      const line = el("div", "cells-row");
      line.appendChild(el("span", "cells-axis", String(r)));
      for (let c = 1; c <= cols; c++) {
        const key = `${r}:${c}`;
        const cell = el("button", "cell" + (chosen.has(key) ? " on" : ""), chosen.has(key) ? "✓" : "");
        cell.type = "button";
        cell.setAttribute("aria-pressed", chosen.has(key) ? "true" : "false");
        cell.title = `рядок ${r}, комірка ${c}`;
        cell.onclick = (e) => {
          e.preventDefault();
          chosen.has(key) ? chosen.delete(key) : chosen.add(key);
          commit();
          redraw();
        };
        line.appendChild(cell);
      }
      const all = el("button", "quiet cells-row-btn", "обрати");
      all.type = "button";
      all.title = "увесь рядок";
      all.onclick = (e) => {
        e.preventDefault();
        const keys = Array.from({ length: cols }, (_, i) => `${r}:${i + 1}`);
        const full = keys.every((k) => chosen.has(k));
        keys.forEach((k) => (full ? chosen.delete(k) : chosen.add(k)));
        commit();
        redraw();
      };
      line.appendChild(all);
      box.appendChild(line);
    }

    const actions = el("div", "cells-actions");
    const all = el("button", "quiet", "обрати все");
    const clear = el("button", "quiet", "очистити");
    all.type = clear.type = "button";
    all.onclick = (e) => {
      e.preventDefault();
      for (let r = 1; r <= rows; r++) for (let c = 1; c <= cols; c++) chosen.add(`${r}:${c}`);
      commit(); redraw();
    };
    clear.onclick = (e) => {
      e.preventDefault();
      chosen.clear(); commit(); redraw();
    };
    actions.append(all, clear, el("span", "cells-count", chosen.size ? `обрано: ${chosen.size}` : "нічого — продаж не запускається"));
    box.appendChild(actions);
  };
  redraw();
  return box;
}

function listInput(value, onChange, placeholder) {
  const box = el("div");
  const data = Array.isArray(value) ? [...value] : [];
  const redraw = () => {
    box.innerHTML = "";
    data.forEach((item, i) => {
      const row = el("div", "kv-row");
      row.style.gridTemplateColumns = "minmax(0, 1fr) 34px";
      const input = el("input");
      input.value = item;
      input.placeholder = placeholder || "";
      input.onchange = () => { data[i] = input.value; onChange([...data]); };
      const del = el("button", "quiet", "×");
      del.title = "прибрати";
      del.onclick = () => { data.splice(i, 1); onChange([...data]); redraw(); };
      row.append(input, del);
      box.appendChild(row);
    });
    const add = el("button", "quiet", "+ додати");
    add.onclick = () => { data.push(""); onChange([...data]); redraw(); };
    box.appendChild(add);
  };
  redraw();
  return box;
}

function buildFields(schema, root, value, onChange, container, opts = {}) {
  Object.entries(schema.properties || {}).forEach(([name, rawSpec]) => {
    if (name.startsWith("_")) return;
    const spec = resolveRef(rawSpec, root);
    // технічне (координати, кольори, пороги розпізнавання) показуємо лише на вимогу
    if (spec.tech && !opts.tech) return;
    const label = spec.title || name;
    // якщо поля нема в збереженому конфізі — показуємо значення за замовчуванням
    // зі схеми, інакше форма виглядала б порожньою
    const current = value[name] !== undefined ? value[name] : spec.default;

    if (spec.type === "object" && spec.properties) {          // вкладений блок
      const group = el("fieldset", "group-box");
      group.appendChild(el("legend", "", label));
      const inner = el("div", "inner");
      if (current && current.enabled === false) {
        const off = el("div", "hint", "блок вимкнений — увімкни перемикач нижче");
        off.style.gridColumn = "1 / -1";
        inner.appendChild(off);
      }
      const nested = { ...(current || {}) };
      buildFields(spec, root, nested, (patch) => {
        Object.assign(nested, patch);
        onChange({ [name]: nested });
      }, inner, opts);
      if (!inner.children.length) return;      // у блоці саме технічне — не показуємо
      group.appendChild(inner);
      container.appendChild(group);
      return;
    }
    if (spec.type === "object" && spec.additionalProperties) { // словник клавіш
      const field = el("div", "field full");
      field.appendChild(el("label", "", label));
      field.appendChild(dictInput(current, (v) => onChange({ [name]: v })));
      container.appendChild(field);
      return;
    }

    if (spec.type === "array" && name === "cells") {            // сітка рюкзака
      const field = el("div", "field full");
      field.appendChild(el("label", "", label));
      field.appendChild(cellsInput(current, (v) => onChange({ [name]: v })));
      if (spec.description) field.appendChild(el("div", "desc", spec.description));
      container.appendChild(field);
      return;
    }

    if (spec.type === "array" && !spec.prefixItems) {          // список назв
      const field = el("div", "field full");
      field.appendChild(el("label", "", label));
      field.appendChild(listInput(current, (v) => {
        // порожній перемикач + заповнений список = мовчазна пастка, тому вмикаємо самі
        const wake = v.some((x) => x && x.trim()) && value.enabled === false;
        onChange(wake ? { [name]: v, enabled: true } : { [name]: v });
        if (wake) toast("увімкнув «" + (schema.title || "блок") + "» — не забудь зберегти", "ok");
      }, "назва моба"));
      if (spec.description) field.appendChild(el("div", "desc", spec.description));
      container.appendChild(field);
      return;
    }

    const field = el("div", "field" + (spec.prefixItems ? " full" : ""));
    field.appendChild(el("label", "", label));
    field.appendChild(spec.prefixItems
      ? tupleInput(spec, current, (v) => onChange({ [name]: v }))
      : inputFor(spec, current, (v) => onChange({ [name]: v })));
    if (spec.description) field.appendChild(el("div", "desc", spec.description));
    container.appendChild(field);
  });
}

/* ---------- дані ---------- */
const metaOf = (type) => S.catalog.find((c) => c.type === type)
  || { schema: {}, provides: [], requires: [], category: "інше", label: type, run_order: 0 };

/* Вікна — це персонажі: онлайн-клієнти від сканера плюс ті, чиї вікна зараз закриті. */
const entries = () => (S.state?.windows || []).filter((w) => !(w.nick && S.removed.has(w.nick) && !w.online));
const currentEntry = () => entries().find((w) => w.name === S.window) || entries()[0] || null;
const charOf = (entry) => (entry && entry.nick ? S.config.characters[entry.nick] : null) || null;
const currentChar = () => charOf(currentEntry());

/* Конструктор за замовчуванням іде за профілем вибраного персонажа, але пул можна
   браузити окремо (вибір у «Конструкторі» -> S.profile) — саме так туди й додають
   профіль, ще не призначений нікому. */
function profileName() {
  if (S.profile && S.config.profiles[S.profile]) return S.profile;
  const own = currentChar()?.profile;
  if (own && S.config.profiles[own]) return own;
  return S.config.default_profile && S.config.profiles[S.config.default_profile]
    ? S.config.default_profile : Object.keys(S.config.profiles)[0];
}
const currentProfile = () => S.config.profiles[profileName()];

const ownerOf = () => {
  const char = currentChar();
  return char && char.profile === profileName() ? char : null;
};

const effectiveConfig = (owner, spec) =>
  ({ ...spec.config, ...(((owner && owner.overrides) || {})[spec.type] || {}) });

/* ---------- вікна (картки персонажів) ---------- */
function statusLine(entry) {
  if (!entry.online) return "вікно закрите";
  if (!entry.nick) return "нік ще не прочитано";
  if (!entry.available) return "вікно згорнуте";
  if (!entry.known) return "новий персонаж";
  if (entry.connected) return `${entry.fps} к/с · тік ${entry.tick}`;
  return entry.error || (entry.enabled ? "чекаю запуску" : "вимкнений");
}

function dotClass(entry) {
  if (entry.connected) return "dot on";
  if (entry.error && entry.enabled) return "dot err";
  return "dot";
}

function renderWindows() {
  const host = $("windowList");
  host.innerHTML = "";
  const list = entries();
  S.cardsKey = cardsKey();
  if (!list.length) {
    host.appendChild(el("p", "hint",
      "клієнтів гри не знайдено. Запусти ElementClient — нік прочитається сам за кілька секунд."));
    return;
  }
  const current = currentEntry();

  list.forEach((entry) => {
    const char = charOf(entry);
    const active = entry.name === current?.name;
    const card = el("div", "window-card" + (active ? " active" : "") + (entry.online ? "" : " offline"));
    card.dataset.name = entry.name;
    card.onclick = (e) => {
      if (e.target.closest("input, button, label, select")) return;
      S.window = entry.name; S.profile = null;
      render();
      refreshPreview();
      refreshEvents();
    };

    const row = el("div", "row");
    const dot = el("span", dotClass(entry));
    dot.dataset.role = "dot";
    const title = el("div", "name", entry.label || entry.name);
    title.style.flex = "1";
    title.style.fontWeight = "600";
    const run = el("button", entry.active ? "danger window-run" : "quiet window-run",
                   entry.active ? "стоп" : "старт");
    run.title = `${entry.active ? "зупинити" : "запустити"} лише цього персонажа`;
    run.disabled = !entry.online || (!entry.enabled && !entry.active);
    run.onclick = async () => {
      const wasActive = entry.active;
      run.disabled = true;
      try {
        if (S.dirty) await saveConfig();
        const action = wasActive ? "stop" : "start";
        const body = wasActive ? {} : { dry_run: $("dryRun").checked };
        S.state = await api.post(`/api/windows/${encodeURIComponent(entry.name)}/${action}`, body);
        render();
      } catch (e) {
        toast(e.message, "err");
        run.disabled = false;
      }
    };
    row.append(dot, title, run);
    if (!entry.online && char) {
      const gone = el("button", "quiet window-remove", "×");
      gone.title = "прибрати зі списку (вікно закрите)";
      gone.onclick = (e) => { e.stopPropagation(); removeCharacter(entry); };
      row.appendChild(gone);
    }
    card.appendChild(row);

    const meta = el("div", "stats");
    meta.dataset.role = "stats";
    card.appendChild(meta);
    fillStats(meta, entry);

    if (entry.online && !entry.nick) card.appendChild(pinControls(entry, true));

    if (char) {
      const line = el("div", "card-line");
      const profile = el("select");
      Object.keys(S.config.profiles).forEach((p) => {
        const o = el("option", "", p); o.value = p; o.selected = p === char.profile;
        profile.appendChild(o);
      });
      profile.title = "профіль цього персонажа";
      profile.onchange = () => {
        char.profile = profile.value;
        markDirty(); render();
      };
      const on = el("label", "chk card-on");
      on.title = "запускати бота для цього персонажа";
      const box = el("input"); box.type = "checkbox"; box.checked = !!char.enabled;
      box.onchange = () => { char.enabled = box.checked; markDirty(); render(); };
      on.append(box, document.createTextNode(" запускати"));
      line.append(profile, on);
      card.appendChild(line);
    }

    if (active && char) card.appendChild(windowSettings(entry, char));
    if (active && entry.online && entry.nick) card.appendChild(pinControls(entry, false));
    host.appendChild(card);
  });
}

/* Закріплення вікна за персонажем. Нік читається з екрана й після підтвердження
   прилипає до вікна; якщо OCR схибив — можна вказати нік руками, і те хибне
   прочитання запам'ятається як псевдонім цього персонажа. */
function pinControls(entry, unread) {
  const box = el("div");
  const known = Object.keys(S.config.characters);
  const note = el("div", "pin-note" + (unread ? " warn" : ""));
  if (unread) {
    note.textContent = entry.raw
      ? `нік не підтверджено (читається як «${entry.raw}»). Можна вказати вручну:`
      : "нік ще не прочитано (екран завантаження?). Можна вказати вручну:";
  } else if (entry.manual) {
    note.textContent = "🔒 нік закріплено вручну за цим вікном";
  } else {
    note.textContent = entry.raw && entry.raw !== entry.nick
      ? `🔒 нік закріплено за цим вікном (OCR бачить «${entry.raw}»)`
      : "🔒 нік закріплено за цим вікном";
  }
  box.appendChild(note);

  const row = el("div", "pin-row");
  const input = el("input");
  input.placeholder = unread ? "нік персонажа" : "не той нік? вкажи правильний";
  const listId = `nicks-${entry.hwnd}`;
  input.setAttribute("list", listId);
  const list = el("datalist"); list.id = listId;
  known.forEach((n) => { const o = el("option"); o.value = n; list.appendChild(o); });
  const go = el("button", "quiet", "закріпити");
  go.onclick = async () => {
    const nick = input.value.trim();
    if (!nick) { toast("впиши нік", "err"); return; }
    try {
      S.state = await api.post(`/api/windows/${encodeURIComponent(entry.name)}/assign`, { nick });
      const fresh = await api.get("/api/config");
      Object.keys(fresh.characters).forEach((k) => { if (!S.config.characters[k]) S.config.characters[k] = fresh.characters[k]; });
      Object.keys(S.config.characters).forEach((k) => { if (!fresh.characters[k] && !S.dirty) delete S.config.characters[k]; });
      S.window = nick; S.profile = null;
      toast(`вікно #${entry.hwnd} закріплено за ${nick}`, "ok");
      render(); refreshPreview(); refreshEvents();
    } catch (e) { toast(e.message, "err"); }
  };
  row.append(input, go);
  box.append(row, list);

  if (!unread) {
    const again = el("button", "quiet", "скинути закріплення й перечитати");
    again.style.marginTop = "6px";
    again.onclick = async () => {
      try {
        S.state = await api.post(`/api/windows/${encodeURIComponent(entry.name)}/unpin`);
        render();
      } catch (e) { toast(e.message, "err"); }
    };
    box.appendChild(again);
  }
  return box;
}

function fillStats(node, entry) {
  node.innerHTML = "";
  const id = entry.hwnd ? `#${entry.hwnd} · ${entry.width}×${entry.height} · ` : "";
  node.appendChild(el("div", "", id + statusLine(entry)));
  if (entry.connected && entry.timing) node.appendChild(el("div", "", "⏱ " + entry.timing));
  if (entry.connected) {
    Object.entries(entry.pipelines || {}).filter(([, v]) => v).slice(0, 3)
      .forEach(([k, v]) => node.appendChild(el("div", "", `${metaOf(k).label}: ${v}`)));
  }
}

/* Кожну секунду оновлюємо лише цифри й крапки, а не перебудовуємо картки:
   інакше відкритий випадаючий список профілю закривався б під рукою. */
function updateWindowStats() {
  entries().forEach((entry) => {
    const card = document.querySelector(`.window-card[data-name="${CSS.escape(entry.name)}"]`);
    if (!card) return;
    const dot = card.querySelector('[data-role="dot"]');
    if (dot) dot.className = dotClass(entry);
    const stats = card.querySelector('[data-role="stats"]');
    if (stats) fillStats(stats, entry);
  });
}

/* Що визначає вигляд карток: набір вікон і їхній стан «онлайн/відомий». Змінилось —
   перебудовуємо, ні — лише оновлюємо цифри. */
function cardsKey() {
  return JSON.stringify([entries().map((e) => [e.name, e.online, e.known, e.nick, e.available, e.pinned, e.manual]),
                         S.window, Object.keys(S.config?.profiles || {})]);
}

function windowSettings(entry, char) {
  const box = el("div");
  box.style.display = "grid";
  box.style.gap = "8px";
  box.style.marginTop = "4px";

  const mk = (label, input) => {
    const f = el("div", "field");
    f.style.gridTemplateColumns = "110px minmax(0, 1fr)";
    f.append(el("label", "", label), input);
    return f;
  };

  const label = el("input");
  label.value = char.label || "";
  label.placeholder = entry.nick;
  label.title = "своя назва в інтерфейсі; нік, за яким бот впізнає персонажа, не міняється";
  label.onchange = () => { char.label = label.value.trim(); markDirty(); render(); };

  const poll = el("input"); poll.type = "number"; poll.step = "0.02"; poll.min = "0.02";
  poll.value = char.poll_interval;
  poll.onchange = () => { char.poll_interval = Number(poll.value); markDirty(); };

  box.append(mk("Назва", label), mk("Кадр, с", poll));

  const nick = el("div", "hint", `нік із екрана: ${entry.nick}`);
  box.appendChild(nick);

  if (!entry.online) {
    const del = el("button", "quiet", "прибрати зі списку");
    del.onclick = () => removeCharacter(entry);
    box.appendChild(del);
  }
  return box;
}

/* Прибрати персонажа, чиє вікно зараз закрите. Зберігається звичайним «Зберегти».
   Без S.removed сервер (де видалення ще не збережене) далі віддавав би цього персонажа
   в /api/state, і adoptNewCharacters() за секунду повертав би його назад — видалення
   здавалось «непрацюючим». */
function removeCharacter(entry) {
  if (!entry || !entry.nick || entry.online) return;
  delete S.config.characters[entry.nick];
  S.removed.add(entry.nick);
  if (S.window === entry.name) S.window = null;
  markDirty();
  render();
  toast(`«${entry.nick}» прибрано зі списку — натисни «Зберегти»`, "ok");
}

/* ---------- пул профілів ---------- */
function usersOf(name) {
  return Object.entries(S.config.characters)
    .filter(([, c]) => c.profile === name).map(([nick, c]) => c.label || nick);
}

function renderProfileBar() {
  const host = $("profileBar");
  host.innerHTML = "";
  const current = profileName();
  const names = Object.keys(S.config.profiles);

  const row = el("div", "profile-row");
  const select = el("select");
  select.title = "переглянути й редагувати інший профіль з пулу";
  names.forEach((n) => {
    const o = el("option", "", n);
    o.value = n; o.selected = n === current;
    select.appendChild(o);
  });
  select.onchange = () => { S.profile = select.value; renderPipelines(); renderProfileBar(); };
  row.appendChild(select);
  host.appendChild(row);

  const actions = el("div", "profile-actions");
  const create = el("button", "quiet", "створити профіль");
  create.title = "нова копія поточного профілю в пул. Є вибраний персонаж — одразу йому "
                + "й призначиться, інакше признач потім вибором у картці зліва";
  create.onclick = () => addProfile(current);

  const rename = el("button", "quiet", "перейменувати");
  rename.title = "перейменувати профіль «" + current + "»";
  rename.onclick = () => renameProfile(current);

  const del = el("button", "quiet", "видалити");
  del.title = "прибрати профіль «" + current + "» із пулу";
  del.disabled = names.length <= 1;
  del.onclick = () => removeProfile(current);

  actions.append(create, rename, del);
  host.appendChild(actions);

  const users = usersOf(current);
  host.appendChild(el("div", "hint",
    users.length ? `використовують: ${users.join(", ")}` : "поки нікому не призначено"));
}

function askProfileName(text, suggestion) {
  const name = (prompt(text, suggestion) || "").trim();
  if (!name) return null;
  if (S.config.profiles[name]) { toast(`профіль «${name}» уже є`, "err"); return null; }
  return name;
}

function addProfile(from) {
  const name = askProfileName("Назва нового профілю (буде копією поточного):", `${from} копія`);
  if (!name) return false;
  S.config.profiles[name] = JSON.parse(JSON.stringify(S.config.profiles[from]));
  S.profile = name;                          // дивимось на щойно створений
  const owner = currentChar();               // є вибраний персонаж — одразу йому й призначаємо
  if (owner) owner.profile = name;
  markDirty(); render();
  toast(owner ? `профіль «${name}» створено для вибраного персонажа`
              : `профіль «${name}» додано в пул — признач його персонажу зліва`, "ok");
  return true;
}

function renameProfile(from) {
  const name = askProfileName(`Нова назва для «${from}»:`, from);
  if (!name) return;
  const renamed = {};
  Object.entries(S.config.profiles).forEach(([key, value]) => {
    renamed[key === from ? name : key] = value;
  });
  S.config.profiles = renamed;
  Object.values(S.config.characters).forEach((char) => {
    if (char.profile === from) char.profile = name;
  });
  S.config.windows.forEach((window) => {
    if (window.profile === from) window.profile = name;
  });
  if (S.config.default_profile === from) S.config.default_profile = name;
  S.profile = name;
  markDirty(); render();
  toast(`профіль «${from}» перейменовано на «${name}»`, "ok");
}

function removeProfile(name) {
  const rest = Object.keys(S.config.profiles).filter((n) => n !== name);
  if (!rest.length) { toast("останній профіль видаляти не можна", "err"); return; }
  const users = usersOf(name);
  const fallback = (S.config.default_profile && S.config.default_profile !== name)
    ? S.config.default_profile : rest[0];
  const warn = users.length
    ? `Профіль «${name}» використовують: ${users.join(", ")}.
Їм призначиться «${fallback}». Видалити?`
    : `Видалити профіль «${name}»?`;
  if (!confirm(warn)) return;
  delete S.config.profiles[name];
  Object.values(S.config.characters).forEach((char) => { if (char.profile === name) char.profile = fallback; });
  S.config.windows.forEach((window) => { if (window.profile === name) window.profile = fallback; });
  if (S.config.default_profile === name) S.config.default_profile = null;
  S.profile = null;
  markDirty(); render();
  toast(`профіль «${name}» видалено`, "ok");
}

/* ---------- конструктор ---------- */
function orderedPipelineItems(profile) {
  return profile.pipelines
    .map((spec, index) => ({ spec, index, runOrder: Number(metaOf(spec.type).run_order || 0) }))
    .sort((a, b) => a.runOrder - b.runOrder || a.index - b.index);
}

function phaseLabel(runOrder) {
  if (runOrder === 0) return "Підготовка й обслуговування";
  if (runOrder === 50) return "Підготовка до бою · форма";
  if (runOrder === 100) return "Пошук цілі · завжди в кінці";
  if (runOrder === 150) return "Збір луту · після пошуку";
  if (runOrder === 200) return "Атака · останній крок";
  return `Етап ${runOrder}`;
}

function movePipeline(profile, index, direction) {
  const ordered = orderedPipelineItems(profile);
  const position = ordered.findIndex((item) => item.index === index);
  const current = ordered[position];
  const target = ordered[position + direction];
  if (!current || !target || current.runOrder !== target.runOrder) return;
  [profile.pipelines[current.index], profile.pipelines[target.index]] =
    [profile.pipelines[target.index], profile.pipelines[current.index]];
  markDirty();
  renderPipelines();
}

function renderPipelines() {
  const host = $("pipelines");
  host.innerHTML = "";
  const entry = currentEntry();
  const profile = currentProfile();
  const owner = ownerOf();
  renderProfileBar();
  $("profileName").textContent = owner
    ? `профіль «${profileName()}» · персонаж ${entry.label || entry.nick}`
    : `профіль «${profileName()}»`;

  const ordered = orderedPipelineItems(profile);
  const phases = new Map();
  ordered.forEach((item) => {
    if (!phases.has(item.runOrder)) phases.set(item.runOrder, []);
    phases.get(item.runOrder).push(item);
  });

  phases.forEach((items, runOrder) => {
      const head = el("div", "group");
      head.append(el("h3", "", phaseLabel(runOrder)), el("div", "line"),
                  el("span", "count", `${items.filter((i) => i.spec.enabled).length}/${items.length}`));
      host.appendChild(head);
      items.forEach(({ spec, index }, position) => host.appendChild(pipelineCard(owner, profile, spec, index, {
        canUp: position > 0,
        canDown: position < items.length - 1,
      })));
  });

  renderManualButtons();
}

function pipelineCard(owner, profile, spec, index, movement) {
  const meta = metaOf(spec.type);
  const hasOverride = !!(owner && (owner.overrides || {})[spec.type]);
  if (S.open[spec.type] === undefined) S.open[spec.type] = false;

  const card = el("div", "pipe" + (spec.enabled ? "" : " off"));
  const head = el("div", "pipe-head");

  head.appendChild(toggle(spec.enabled, (v) => { spec.enabled = v; markDirty(); renderPipelines(); }));

  const titleBox = el("div");
  titleBox.append(el("div", "name", meta.label || spec.type),
                  el("div", "sub", `${CATEGORY[meta.category] || meta.category || "Інше"} · ${spec.type}` +
                    (hasOverride ? " · своє для цього персонажа" : "")));
  head.appendChild(titleBox);

  const wires = el("div", "wires");
  meta.requires.forEach((r) => wires.appendChild(el("span", "chip in", `← ${r}`)));
  meta.provides.forEach((p) => wires.appendChild(el("span", "chip out", `${p} →`)));
  if (!meta.requires.length && !meta.provides.length) wires.appendChild(el("span", "chip", "самостійний"));
  head.appendChild(wires);

  const order = el("div", "pipe-order");
  const up = el("button", "quiet order-btn", "↑");
  const down = el("button", "quiet order-btn", "↓");
  up.type = down.type = "button";
  up.title = "виконувати раніше";
  down.title = "виконувати пізніше";
  up.disabled = !movement.canUp;
  down.disabled = !movement.canDown;
  up.onclick = () => movePipeline(profile, index, -1);
  down.onclick = () => movePipeline(profile, index, 1);
  order.append(up, down);
  head.appendChild(order);

  const fold = el("button", "quiet", S.open[spec.type] ? "згорнути" : "налаштувати");
  head.appendChild(fold);
  card.appendChild(head);

  const body = el("div", "pipe-body");
  body.hidden = !S.open[spec.type];
  const value = effectiveConfig(owner, spec);

  if (owner) {
    const ovr = el("div", "field full");
    ovr.append(el("label", "", "Своє для цього персонажа"),
      toggle(hasOverride, (v) => {
        owner.overrides = owner.overrides || {};
        if (v) owner.overrides[spec.type] = { ...value };
        else delete owner.overrides[spec.type];
        markDirty(); renderPipelines();
      }));
    body.appendChild(ovr);
  }

  const apply = (patch) => {
    Object.assign(value, patch);
    if (hasOverride) owner.overrides[spec.type] = { ...value };
    else profile.pipelines[index].config = { ...value };
    markDirty();
  };
  buildFields(meta.schema, meta.schema, value, apply, body, { tech: S.tech });

  fold.onclick = () => {
    S.open[spec.type] = body.hidden;
    body.hidden = !body.hidden;
    fold.textContent = body.hidden ? "налаштувати" : "згорнути";
  };

  card.appendChild(body);
  return card;
}

/* ---------- ручні кнопки ---------- */
function renderManualButtons() {
  const host = $("manualButtons");
  host.innerHTML = "";
  if (!currentEntry()) return;

  const owner = ownerOf();
  const buttons = [];
  currentProfile().pipelines.forEach((spec) => {
    const cfg = effectiveConfig(owner, spec);
    const add = (label, key, times = 1, interval = 0.3) => buttons.push({ label, key, times, interval });
    if (spec.type === "attack" && cfg.key) add(`Атака · ${cfg.key}`, cfg.key);
    if (spec.type === "target_search" && cfg.target_key) add(`Ціль · ${cfg.target_key}`, cfg.target_key);
    if (spec.type === "loot" && cfg.key) add(`Лут · ${cfg.key} ×${cfg.max_presses}`, cfg.key, cfg.max_presses, cfg.interval);
    if (spec.type === "form_keep" && cfg.key) add(`Форма · ${cfg.key}`, cfg.key);
    if (spec.type === "pet_heal" && cfg.heal_key) add(`Лік пета · ${cfg.heal_key}`, cfg.heal_key);
    if (spec.type === "repair" && cfg.bag_key) add(`Рюкзак · ${cfg.bag_key}`, cfg.bag_key);
    if (spec.type === "periodic_keys") Object.keys(cfg.keys || {}).forEach((k) => add(`Клавіша ${k}`, k));
  });

  buttons.forEach(({ label, key, times, interval }) => {
    const b = el("button", "", label);
    b.onclick = () => press(key, times, interval);
    host.appendChild(b);
  });
}

async function press(key, times = 1, interval = 0.3) {
  const entry = currentEntry();
  if (!entry || !entry.online || !key) { toast("вікно цього персонажа не знайдено", "err"); return; }
  try {
    await api.post(`/api/windows/${encodeURIComponent(entry.name)}/press`, { key, times, interval });
    toast(`${key} ×${times}`, "ok");
  } catch (e) { toast(e.message, "err"); }
}

/* ---------- превʼю ---------- */
let previewBusy = false;
const PREVIEW_SCALE = 0.5;         // масштаб кадру в превʼю

async function refreshPreview() {
  const entry = currentEntry();
  const img = $("preview");
  if (!entry || !entry.online) {
    img.hidden = true;
    $("previewEmpty").hidden = false;
    $("previewEmpty").textContent = entry ? "вікно гри закрите" : "вікон нема";
    return;
  }
  if (previewBusy) return;
  previewBusy = true;
  const overlay = $("overlayLoot").checked ? "loot" : "";
  const url = `/api/windows/${encodeURIComponent(entry.name)}/frame.png`
    + `?overlay=${overlay}&scale=${PREVIEW_SCALE}&t=${Date.now()}`;
  try {
    await new Promise((resolve, reject) => {
      const probe = new Image();
      probe.onload = () => { img.src = probe.src; resolve(); };
      probe.onerror = () => reject(new Error("кадр не отримано"));
      probe.src = url;
    });
    img.hidden = false;
    $("previewEmpty").hidden = true;
    if (overlay) {
      const r = await api.get(`/api/windows/${encodeURIComponent(entry.name)}/loot-check`);
      $("lootCheck").textContent = r.enabled
        ? (r.has_loot ? `на землі щось лежить · підписів ${r.labels}` : "на землі порожньо")
        : "перевірка землі вимкнена";
    } else {
      $("lootCheck").textContent = "";
    }
  } catch (e) {
    img.hidden = true;
    $("previewEmpty").hidden = false;
    $("previewEmpty").textContent = "вікно гри недоступне";
  } finally {
    previewBusy = false;
  }
}

/* ---------- стан ---------- */
/* Сканер міг додати нового персонажа вже після того, як сторінку завантажено. Дописуємо
   лише відсутніх, щоб не затерти те, що користувач уже змінив і ще не зберіг. */
async function adoptNewCharacters() {
  const missing = entries().filter((e) => e.known && e.nick && !S.config.characters[e.nick]
                                      && !S.removed.has(e.nick));
  if (!missing.length) return false;
  const fresh = await api.get("/api/config");
  let added = false;
  missing.forEach((e) => {
    if (fresh.characters[e.nick]) { S.config.characters[e.nick] = fresh.characters[e.nick]; added = true; }
  });
  return added;
}

async function refreshState() {
  try {
    S.state = await api.get("/api/state");
    const on = S.state.running;
    $("runState").textContent = on ? (S.state.dry_run ? "працює без клавіш" : "працює") : "зупинено";
    $("runState").className = "pill " + (on ? "on" : "off");
    $("btnStart").disabled = on;
    $("btnStop").disabled = !on;
    const added = await adoptNewCharacters();
    if (added || S.cardsKey !== cardsKey()) {
      if (!S.window || !entries().some((e) => e.name === S.window)) S.window = entries()[0]?.name || null;
      render();
    } else {
      updateWindowStats();
    }
  } catch { /* сервер міг перезапускатись */ }
}

async function rescan() {
  $("btnDiscover").disabled = true;
  try {
    S.state = await api.post("/api/scan");
    await adoptNewCharacters();
    render();
    refreshPreview();
    refreshEvents();
  } catch (e) { toast(e.message, "err"); }
  finally { $("btnDiscover").disabled = false; }
}

function renderAppSettings() {
  const host = $("appSettings");
  host.innerHTML = "";
  if (!S.appSchema || !S.config.settings) return;
  buildFields(S.appSchema, S.appSchema, S.config.settings, (patch) => {
    Object.assign(S.config.settings, patch);
    markDirty();
  }, host, { tech: S.tech });
}

function render() {
  renderWindows();
  renderAppSettings();
  renderPipelines();
}

async function load() {
  [S.config, S.catalog, S.state, S.appSchema] = await Promise.all(
    [api.get("/api/config"), api.get("/api/catalog"), api.get("/api/state"),
     api.get("/api/settings-schema")]);
  S.window = entries()[0]?.name || null;
  render();
  refreshPreview();
  refreshEvents();
}

/* ---------- події ---------- */
function eventLine(e) {
  const t = new Date(e.ts * 1000).toLocaleTimeString("uk-UA", { hour12: false });
  const warn = e.level === "WARNING" || e.level === "ERROR" || e.message.startsWith("!!");
  const row = el("div", "event-line" + (warn ? " warn" : ""));
  row.append(el("span", "t", t), el("span", "w", `[${e.window}]`), el("span", "", e.message));
  return row;
}

async function refreshEvents() {
  const host = $("eventList");
  if (!host) return;
  const all = $("eventsAll").checked;
  const entry = currentEntry();
  $("eventsWho").textContent = all ? "усі персонажі" : (entry ? (entry.label || entry.name) : "нема вікна");
  if (!all && !entry) { host.innerHTML = ""; host.appendChild(el("div", "events-empty", "вибери вікно зліва")); return; }
  const params = new URLSearchParams({ limit: "150" });
  if (!all) params.set("window", entry.name);
  try {
    const events = await api.get(`/api/log?${params}`);
    host.innerHTML = "";
    if (!events.length) { host.appendChild(el("div", "events-empty", "поки що тихо")); return; }
    events.forEach((e) => host.appendChild(eventLine(e)));
  } catch (e) { /* сервер міг перезапускатись */ }
}

$("btnSave").onclick = async () => {
  try { await saveConfig(); }
  catch (e) { toast(e.message, "err"); }
};

async function saveConfig() {
  S.config = await api.put("/api/config", S.config);
  S.removed.clear();              // сервер уже без них — ховати більше нема чого
  S.dirty = false;
  S.warnedDirty = false;
  $("btnSave").disabled = true;
  $("btnSave").textContent = "Зберегти";
  toast("збережено" + (S.state?.running ? " · бот перезапущено" : ""), "ok");
  await refreshState();
  render();
}

$("btnStart").onclick = async () => {
  try {
    if (S.dirty) await saveConfig();
    S.state = await api.post("/api/control/start", { dry_run: $("dryRun").checked });
    if (!entries().some((e) => e.enabled && e.online)) {
      toast("жоден персонаж не увімкнений — постав «запускати» біля потрібного", "err");
    }
    await refreshState();
  } catch (e) { toast(e.message, "err"); }
};
$("btnStop").onclick = async () => {
  try { await api.post("/api/control/stop"); await refreshState(); }
  catch (e) { toast(e.message, "err"); }
};
$("preview").onclick = async (e) => {
  if (!$("calibMode").checked) return;
  const entry = currentEntry();
  if (!entry || !entry.online) return;
  // превʼю зменшене, тому переводимо клік у координати самої гри
  const rect = e.target.getBoundingClientRect();
  const x = Math.round((e.clientX - rect.left) / rect.width * e.target.naturalWidth / PREVIEW_SCALE);
  const y = Math.round((e.clientY - rect.top) / rect.height * e.target.naturalHeight / PREVIEW_SCALE);
  try {
    const r = await api.post(`/api/windows/${encodeURIComponent(entry.name)}/calibrate`,
                             { what: $("calibWhat").value, x, y });
    toast(`${r.title}: (${r.x}, ${r.y})`, "ok");
    const keepDirty = S.dirty;
    const fresh = await api.get("/api/config");
    S.config.profiles = fresh.profiles;               // калібрування пише в профіль на сервері
    if (!keepDirty) S.config = fresh;
    render();
  } catch (err) { toast(err.message, "err"); }
};
$("techMode").onchange = () => { S.tech = $("techMode").checked; renderPipelines(); renderAppSettings(); };
$("btnDiscover").onclick = rescan;
$("btnShot").onclick = refreshPreview;
$("btnCustom").onclick = () => press(
  $("customKey").value.trim(),
  Number($("customTimes").value) || 1,
  Number($("customInterval").value) || 0.3);
$("overlayLoot").onchange = refreshPreview;
$("autoRefresh").onchange = () => {
  clearInterval(S.timer);
  if ($("autoRefresh").checked) S.timer = setInterval(refreshPreview, 1500);
};

$("btnEventsRefresh").onclick = refreshEvents;
$("eventsAll").onchange = refreshEvents;

setInterval(refreshState, 1000);
setInterval(refreshEvents, 2000);
load().catch((e) => toast(e.message, "err"));
