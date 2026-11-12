/* SPDX-License-Identifier: MIT */
"use strict";

(() => {
  const API = "/api/foslib";
  const state = { me: null, sequence: 0, page: "dashboard", query: "", cataloguePage: 1 };
  const app = document.getElementById("app");
  const dialog = document.getElementById("action-dialog");
  const paths = {
    book: ["M3 4h6a4 4 0 0 1 3 1.4A4 4 0 0 1 15 4h6v15h-6a4 4 0 0 0-3 1.4A4 4 0 0 0 9 19H3z", "M12 5.4v15"],
    grid: ["M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z"],
    search: ["M21 21l-5-5", "M18 10.5a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0"],
    copies: ["M7 3h11v14H7z", "M4 7v14h11", "M10 7h5M10 10h5"],
    users: ["M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M22 21v-2a4 4 0 0 0-3-3.87", "M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0M16 3a4 4 0 0 1 0 8"],
    checkout: ["M3 5h11v14H3zM17 8l4 4-4 4M10 12h11"],
    return: ["M9 5H3v14h11v-4M17 4l-5 5 5 5M12 9h5a4 4 0 0 1 0 8"],
    clock: ["M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M12 7v5l3 2"],
    fine: ["M5 3h14v18l-3-2-4 2-4-2-3 2zM9 7h6M9 11h6M9 15h3"],
    history: ["M3 11a9 9 0 1 1 2 7M3 4v7h7M12 7v5l3 2"],
    school: ["M3 10l9-7 9 7v11H3zM9 21v-7h6v7M9 9h6"],
    shield: ["M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6zM8 12l3 3 5-6"],
    plus: ["M12 5v14M5 12h14"],
    arrow: ["M5 12h14M14 7l5 5-5 5"],
    left: ["M19 12H5M10 7l-5 5 5 5"],
    close: ["M6 6l12 12M6 18L18 6"],
    check: ["M5 12l4 4L19 6"],
    menu: ["M4 6h16M4 12h16M4 18h16"],
    edit: ["M14 5l5 5M4 20l5-1L21 7a2 2 0 0 0-4-4L5 15z"],
    trash: ["M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"],
    info: ["M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M12 11v6M12 7v.1"]
  };

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value === undefined || value === null || value === false) return;
      if (key === "className") node.className = value;
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else if (key === "checked") node.checked = Boolean(value);
      else if (key === "value") node.value = value;
      else node.setAttribute(key, value === true ? "" : String(value));
    });
    children.flat(Infinity).forEach(child => {
      if (child !== null && child !== undefined && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    });
    return node;
  }

  function icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("class", "icon"); svg.setAttribute("aria-hidden", "true");
    (paths[name] || paths.book).forEach(value => { const p = document.createElementNS(svg.namespaceURI, "path"); p.setAttribute("d", value); svg.append(p); });
    return svg;
  }

  function btn(label, action, options = {}) {
    return el("button", { type: "button", className: `btn ${options.secondary ? "btn-secondary" : ""} ${options.small ? "btn-small" : ""} ${options.danger ? "btn-danger" : ""}`, onclick: action, disabled: options.disabled }, options.icon && icon(options.icon), label);
  }

  function link(label, hash, className = "text-link", iconName) {
    return el("a", { href: `#${hash}`, className }, label, iconName && icon(iconName));
  }

  function cookie(name) {
    const item = document.cookie.split(";").map(value => value.trim()).find(value => value.startsWith(`${name}=`));
    return item ? decodeURIComponent(item.slice(name.length + 1)) : "";
  }

  async function api(path, method = "GET", data) {
    const headers = { Accept: "application/json" };
    if (method !== "GET") {
      headers["Content-Type"] = "application/json";
      headers["X-Foslib-CSRF"] = state.me?.csrf_token || "";
      headers.X_CSRFTOKEN = cookie("csrftoken");
    }
    const response = await fetch(API + path, { method, headers, credentials: "same-origin", body: data === undefined ? undefined : JSON.stringify(data) });
    let payload;
    try { payload = await response.json(); } catch { payload = {}; }
    if (!response.ok) {
      const error = new Error(payload.message || payload.description || payload.error || (response.status === 401 ? "Your session has ended. Please sign in again." : "We could not complete that request. Please try again."));
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function notify(message, failed = false) {
    const container = document.getElementById("notifications");
    const toast = el("div", { className: `toast ${failed ? "toast-error" : ""}`, role: failed ? "alert" : "status" }, icon(failed ? "info" : "check"), el("p", {}, message));
    toast.append(el("button", { type: "button", "aria-label": "Dismiss notification", onclick: () => toast.remove() }, icon("close")));
    container.append(toast);
    setTimeout(() => toast.remove(), 7000);
  }

  const school = () => state.me?.schools?.find(item => String(item.id) === String(state.me.selected_tenant_id));
  const canWrite = () => ["librarian", "worker"].includes(school()?.role);
  const isAdmin = () => Boolean(state.me?.platform_admin);
  const user = () => state.me?.user || {};
  const prettyDate = value => { if (!value) return "—"; const date = new Date(value); return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat("en", { day: "numeric", month: "short", year: "numeric" }).format(date); };
  const money = cents => new Intl.NumberFormat("en", { style: "currency", currency: "USD" }).format(Number(cents || 0) / 100);
  const items = response => Array.isArray(response) ? response : response.items || [];
  const nameOf = record => record.book_title || record.title || "Library book";
  const go = page => { if (location.hash === `#${page}`) render(); else location.hash = page; };

  function brand() {
    return el("a", { href: "#dashboard", className: "brand-link", "aria-label": "FOS Library Management System home" }, el("span", { className: "brand-logo" }, icon("book")), el("span", {}, el("span", { className: "brand-name" }, "FOS"), el("span", { className: "brand-subtitle" }, "Library Management System")));
  }

  function showLogin(message) {
    state.me = null;
    app.replaceChildren(el("div", { className: "login-screen" },
      el("section", { className: "login-brand" }, brand(), el("span", { className: "eyebrow", style: "color:var(--accent-light)" }, "A little curiosity. A world of possibility."), el("h1", {}, "Good books. ", el("em", {}, "Great beginnings.")), el("p", {}, "One shared catalogue. A library that belongs to your school. Bring your reading community together with FOS.")),
      el("main", { className: "login-main", id: "main-content" }, el("span", { className: "eyebrow" }, "FOS Library Management System"), el("h2", {}, "Welcome to your library"), el("p", {}, message || "Sign in to discover books, look after your collection and keep every reader’s next chapter moving."), el("a", { className: "btn", href: "/account/login/?next=/" }, "Sign in to your account", icon("arrow")), el("div", { className: "login-note" }, el("strong", {}, "A shared love of learning"), "Your school’s books, members and circulation are available through your authorised school account."))));
  }

  function mountShell() {
    const sidebar = el("aside", { className: "sidebar", id: "sidebar", "aria-label": "Main navigation" }, brand(), el("div", { className: "sidebar-rule" }));
    let menuToggle;
    const navBackdrop = el("button", { type: "button", className: "nav-backdrop", hidden: true, "aria-label": "Close navigation", tabindex: "-1", onclick: () => setNavigation(false) });
    function setNavigation(open) {
      sidebar.classList.toggle("is-open", open);
      navBackdrop.hidden = !open;
      menuToggle?.setAttribute("aria-expanded", String(open));
    }
    sidebar.prepend(el("button", { type: "button", className: "sidebar-close", "aria-label": "Close navigation", onclick: () => { setNavigation(false); menuToggle?.focus(); } }, icon("close")));
    const nav = el("nav", {});
    const group = (label, entries) => {
      nav.append(el("p", { className: "nav-label" }, label), el("div", { className: "nav-group" }, entries.map(([key, title, symbol]) => el("a", { href: `#${key}`, className: "nav-link", "data-page": key }, icon(symbol), title))));
    };
    group("Your library", [["dashboard", "Overview", "grid"], ["catalogue", "Shared catalogue", "search"]]);
    if (school()) group("School library", [["inventory", "Book copies", "copies"], ["members", "Members & students", "users"], ["loans", "Loans & returns", "checkout"], ["reservations", "Reservations", "clock"], ["fines", "Fines & payments", "fine"], ["transactions", "Transaction history", "history"]]);
    if (isAdmin()) group("Platform", [["schools", "School administration", "school"], ["audit", "Activity & audit", "shield"]]);
    sidebar.append(nav, el("div", { className: "sidebar-bottom" }, el("strong", {}, "Every reader has a next chapter."), el("p", {}, "A little care for your collection. A lasting love of learning."), el("div", { className: "sidebar-footer-mark" }, el("span"), "Friends of School · FOS Library")));
    menuToggle = el("button", { type: "button", className: "mobile-menu", "aria-label": "Toggle navigation", "aria-controls": "sidebar", "aria-expanded": "false", onclick: () => setNavigation(!sidebar.classList.contains("is-open")) }, icon("menu"));
    const context = el("div", { className: "context-switch" }, menuToggle, el("span", { className: "school-icon" }, icon(isAdmin() && !school() ? "shield" : "school")));
    const contextText = el("div", {}, el("span", { className: "context-label" }, school() ? "Your school" : "Platform workspace"));
    if ((state.me.schools || []).length > 1) {
      const select = el("select", { className: "school-select", "aria-label": "Select an authorised school" }, state.me.schools.map(item => el("option", { value: item.id }, item.name)));
      select.value = state.me.selected_tenant_id || "";
      select.addEventListener("change", async () => {
        select.disabled = true;
        try {
          dialog.close(); state.sequence += 1;
          document.getElementById("page-content").replaceChildren(loading("Switching school…"));
          await api("/school", "POST", { tenant_id: select.value });
          state.me = await api("/me"); mountShell(); go("dashboard");
          notify(`Switched to ${school()?.name || "your school"}.`);
        } catch (error) { notify(error.message, true); state.me = await api("/me"); mountShell(); render(); }
      });
      contextText.append(select);
    } else contextText.append(el("span", { className: "school-name" }, school()?.name || "FOS platform"));
    context.append(contextText);
    const displayName = user().name || user().email || "Library account";
    const initials = displayName.split(/[\s@.]+/).filter(Boolean).slice(0, 2).map(part => part[0]).join("").toUpperCase();
    const topbar = el("header", { className: "topbar" }, context, el("div", { className: "topbar-user" }, el("span", { className: "user-avatar", "aria-hidden": "true" }, initials), el("div", { className: "user-info" }, el("span", { className: "user-name" }, displayName), el("span", { className: "user-role" }, isAdmin() ? "Platform administrator" : canWrite() ? "School librarian" : "School reader")), el("a", { href: "/account/logout/?next=/", className: "sign-out" }, "Sign out")));
    const main = el("main", { className: "main-content", id: "main-content", tabindex: "-1" }, el("div", { className: "breadcrumb" }, el("span", {}, "FOS Library"), el("span", { "aria-hidden": "true" }, "/"), el("span", { id: "breadcrumb-current" }, "Overview")), el("div", { id: "page-content" }), el("footer", { className: "page-footer" }, el("span", {}, el("strong", {}, "FOS Library Management System"), " · Growing a community of readers"), el("span", {}, school()?.name || "Shared catalogue. Connected schools.")));
    app.replaceChildren(navBackdrop, sidebar, el("div", { className: "app-shell" }, topbar, main));
    sidebar.querySelectorAll("a").forEach(anchor => anchor.addEventListener("click", () => setNavigation(false)));
    render();
  }

  function heading(title, subtitle, actions = [], eyebrow) {
    return el("div", { className: "page-heading" }, el("div", {}, eyebrow && el("span", { className: "eyebrow" }, eyebrow), el("h1", {}, title), el("p", {}, subtitle)), actions.length && el("div", { className: "heading-actions" }, actions));
  }

  function loading(label = "Loading your library…") { return el("div", { className: "loading-panel", "aria-busy": "true" }, el("span", { className: "spinner", "aria-hidden": "true" }), label); }
  function empty(title, copy, action, symbol = "book") { return el("div", { className: "empty-state" }, icon(symbol), el("h3", {}, title), el("p", {}, copy), action); }
  function panel(title, body, action) { return el("section", { className: "panel" }, title && el("div", { className: "panel-head" }, el("h2", {}, title), action), body); }
  function badge(text, variant = "") { return el("span", { className: `badge ${variant ? `badge-${variant}` : ""}` }, text); }
  function statusBadge(record) {
    const value = record.state || record.status || (record.active === false ? "inactive" : "active");
    const labels = { ITEM_ON_LOAN: "On loan", ITEM_RETURNED: "Returned", CAN_CIRCULATE: "Available", pending: "Pending", fulfilled: "Fulfilled", cancelled: "Cancelled", unpaid: "Unpaid", paid: "Paid", active: "Active", inactive: "Inactive", FOR_REFERENCE_ONLY: "Reference only", MISSING: "Missing", IN_BINDING: "In binding", SCANNING: "Scanning" };
    return badge(labels[value] || value.replaceAll("_", " ").toLowerCase(), ["ITEM_ON_LOAN", "pending", "unpaid"].includes(value) ? "warning" : ["cancelled", "inactive", "FOR_REFERENCE_ONLY", "IN_BINDING", "SCANNING"].includes(value) ? "neutral" : value === "MISSING" ? "danger" : "");
  }

  function table(columns, rows, emptyCopy = "New activity will appear here.") {
    if (!rows.length) return empty("Nothing here yet", emptyCopy);
    return el("div", { className: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, columns.map(column => el("th", { scope: "col" }, column.label)))), el("tbody", {}, rows.map(row => el("tr", {}, columns.map(column => el("td", {}, column.render ? column.render(row) : row[column.key] ?? "—")))))));
  }

  const titleCell = (primary, secondary) => el("span", {}, el("span", { className: "table-title" }, primary || "—"), secondary && el("span", { className: "table-secondary" }, secondary));

  function cover(book, large = false) {
    const colors = ["#0b5d66", "#997748", "#72596b", "#69856b", "#486778", "#a66c4c"];
    const hash = [...book.title].reduce((value, char) => value + char.charCodeAt(0), 0);
    return el("div", { className: large ? "detail-cover" : "book-art", "aria-hidden": "true" }, el("div", { className: "book-cover", style: `--cover:${colors[hash % colors.length]}` }, el("small", {}, "FOS Library"), el("span", { className: "book-cover-title" }, book.title), icon("book")));
  }

  function bookCard(book) {
    return el("article", { className: "book-card" }, el("a", { href: `#book/${book.id}`, tabindex: "-1", "aria-hidden": "true" }, cover(book)), el("div", { className: "book-card-body" }, el("span", { className: "category" }, "Shared catalogue"), el("h3", {}, el("a", { href: `#book/${book.id}` }, book.title)), el("p", { className: "book-author" }, (book.authors || []).join(", ")), el("div", { className: "book-card-meta" }, el("span", {}, book.publication_year || "Book"), el("a", { href: `#book/${book.id}`, "aria-label": `View ${book.title}` }, "View book →"))));
  }

  async function dashboard() {
    const data = await api("/dashboard");
    const catalogue = await api("/catalogue?page=1");
    const root = el("div", {}, heading(isAdmin() && !school() ? "Your library network, at a glance" : "A good day for a new chapter", school() ? `Here’s what’s happening at ${school().name}.` : "Look after your shared catalogue and the schools that bring it to life.", canWrite() ? [btn("Issue a book", () => checkoutForm(), { icon: "checkout" })] : isAdmin() ? [btn("Manage catalogue", () => catalogueForm(), { icon: "plus" })] : [], isAdmin() && !school() ? "Platform overview" : "Library overview"));
    const network = isAdmin() && !school() ? items(await api("/admin/schools")) : [];
    const values = school() ? [["Book copies", data.copies, "copies", "In your school collection"], ["Library members", data.members, "users", "Readers in your community"], ["Books on loan", data.active_loans, "checkout", "Currently with your readers"], ["Reservations", data.pending_holds, "clock", "Waiting for their next chapter"]] : [["Shared books", data.catalogue_count ?? catalogue.total, "book", "Available across your network"], ["Connected schools", network.length, "school", "Libraries in the FOS network"], ["Active schools", network.filter(item => item.active).length, "copies", "School libraries ready to grow"], ["Your memberships", (state.me.schools || []).length, "shield", "Authorised school memberships"]];
    root.append(el("div", { className: "stats" }, values.map(([label, value, symbol, caption]) => el("div", { className: "stat-card" }, el("div", { className: "stat-top" }, el("span", { className: "stat-title" }, label), el("span", { className: "stat-icon" }, icon(symbol))), el("div", { className: "stat-number", style: typeof value === "string" ? "font-size:24px" : undefined }, value ?? 0), el("p", { className: "stat-caption" }, caption)))));
    root.append(el("section", { className: "welcome-panel" }, el("div", { className: "welcome-copy" }, el("span", { className: "eyebrow" }, "Open a book. Open a possibility."), el("h2", {}, "One catalogue. Countless discoveries."), el("p", {}, "Find the familiar favourites and the unexpected discoveries. Every school shares the catalogue, with a collection and reading community of its own."), link("Explore the catalogue", "catalogue", "btn", "arrow")), el("div", { className: "welcome-books", "aria-hidden": "true" }, el("span"), el("span"), el("span"))));
    if (school()) {
      const loans = items(await api("/loans")).filter(loan => loan.state === "ITEM_ON_LOAN");
      const quick = [["Register a book copy", "Add the next book to your school’s shelves.", "copies", () => copyForm()], ["Welcome a new reader", "Register a member or student.", "users", () => memberForm()], ["Return a book", "Close a loan and make room for the next reader.", "return", () => go("loans")]];
      root.append(el("div", { className: "dashboard-columns" }, panel("Currently on loan", table([{ label: "Book", render: row => titleCell(nameOf(row), row.member_name || "Library member") }, { label: "Due back", render: row => prettyDate(row.end_date) }, { label: "Status", render: statusBadge }], loans.slice(0, 4), "Your next checkout will appear here."), link("View loans", "loans", "text-link", "arrow")), panel("A little library care", el("div", { className: "quick-links" }, quick.filter((_, index) => canWrite() || index === 2).map(([title, copy, symbol, action]) => el("a", { href: "#", className: "quick-link", onclick: event => { event.preventDefault(); action(); } }, el("span", { className: "quick-link-icon" }, icon(symbol)), el("span", {}, el("strong", {}, title), el("small", {}, copy)), icon("arrow")))))));
    }
    root.append(el("div", { className: "section-title" }, el("h2", {}, "Fresh inspiration for your shelves"), link("View all books", "catalogue", "text-link", "arrow")), el("div", { className: "books-grid" }, items(catalogue).slice(0, 4).map(bookCard)));
    return root;
  }

  async function cataloguePage(params) {
    const query = params.get("q") || ""; const page = Math.max(1, Number(params.get("page")) || 1);
    const data = await api(`/catalogue?q=${encodeURIComponent(query)}&page=${page}`);
    const input = el("input", { type: "text", name: "q", value: query, placeholder: "Search by title, author or ISBN", "aria-label": "Search the shared catalogue", maxlength: "200" });
    const search = el("form", { className: "search-form", onsubmit: event => { event.preventDefault(); go(`catalogue?q=${encodeURIComponent(input.value.trim())}&page=1`); } }, el("div", { className: "search-wrap" }, icon("search"), input), el("button", { className: "btn btn-primary", type: "submit" }, "Search"));
    const root = el("div", {}, heading("A world of books, shared", "Discover titles in the FOS catalogue, then add the copies that belong on your school’s shelves.", isAdmin() ? [btn("Add catalogue book", () => catalogueForm(), { icon: "plus" })] : [], "Shared catalogue"), el("div", { className: "catalogue-tools" }, search, el("span", { className: "result-count", role: "status" }, `${Number(data.total || 0).toLocaleString()} ${data.total === 1 ? "book" : "books"}${query ? " found" : " to discover"}`)));
    root.append(items(data).length ? el("div", { className: "books-grid" }, items(data).map(bookCard)) : empty("No books found", "Try a different title, author or ISBN. Your next discovery may be one search away.", btn("Browse all books", () => go("catalogue"), { secondary: true }), "search"));
    const perPage = data.per_page || data.page_size || 40;
    root.append(el("div", { className: "pagination" }, btn("Previous", () => go(`catalogue?q=${encodeURIComponent(query)}&page=${page - 1}`), { secondary: true, small: true, disabled: page <= 1 }), el("span", {}, `Page ${page}`), btn("Next", () => go(`catalogue?q=${encodeURIComponent(query)}&page=${page + 1}`), { secondary: true, small: true, disabled: items(data).length === 0 || page * perPage >= data.total })));
    return root;
  }

  async function bookDetail(id) {
    const data = await api(`/catalogue/${encodeURIComponent(id)}`); const book = data.book || data;
    const actions = [];
    if (canWrite()) actions.push(btn("Register a copy", () => copyForm(book), { icon: "plus" }), btn("Reserve this book", () => reservationForm(book), { secondary: true, icon: "clock" }));
    if (isAdmin()) actions.push(btn("Edit catalogue book", () => catalogueForm(book), { secondary: true, icon: "edit" }));
    const root = el("div", {}, el("p", {}, link("Back to catalogue", "catalogue", "text-link", "left")), heading(book.title, (book.authors || []).join(", "), actions, "Shared catalogue · Book details"));
    const terms = [["ISBN", (book.isbn || []).join(", ") || "Not provided"], ["Publisher", book.publisher || "Not provided"], ["Published", book.publication_year || "Not provided"], ["Edition", book.edition || "Not provided"], ["Catalogue source", book.source || "FOS catalogue"]];
    root.append(el("div", { className: "detail-layout" }, cover(book, true), el("div", {}, el("span", { className: "school-context" }, icon("book"), "Shared across FOS schools"), el("dl", { className: "details-grid" }, terms.map(([term, value]) => el("div", {}, el("dt", {}, term), el("dd", {}, value)))), book.description && el("p", { className: "book-description" }, book.description), (book.subjects || []).length && el("div", { className: "subject-list" }, book.subjects.slice(0, 12).map(subject => el("span", { className: "subject-tag" }, subject))))));
    if (school()) root.append(panel(`Copies at ${school().name}`, table([{ label: "Barcode", key: "barcode" }, { label: "Availability", render: row => row.checked_out ? badge("On loan", "warning") : statusBadge(row) }], data.copies || [], "This school has not registered a copy of this book yet."), canWrite() && btn("Register a copy", () => copyForm(book), { small: true, icon: "plus" })));
    return root;
  }

  async function inventoryPage() {
    const records = items(await api("/inventory"));
    return el("div", {}, heading("Your shelves, well looked after", "Register, edit and manage the physical book copies that belong to your school.", canWrite() ? [btn("Register a copy", () => copyForm(), { icon: "plus" })] : [], "School inventory"), panel("Book copies", table([{ label: "Book", render: row => titleCell(nameOf(row), row.description) }, { label: "Barcode", key: "barcode" }, { label: "Availability", render: row => row.checked_out ? badge("On loan", "warning") : statusBadge(row) }, { label: "Actions", render: row => el("div", { className: "row-actions" }, canWrite() && btn("Edit", () => copyEditForm(row), { small: true, secondary: true }), canWrite() && btn("Remove", () => removeRecord("inventory", row, "book copy", row.barcode), { small: true, danger: true })) }], records, "Register your first copy from the shared catalogue to start building your school’s collection.")));
  }

  async function membersPage() {
    const records = items(await api("/members"));
    return el("div", {}, heading("A community of readers", "Keep your school’s students and library members ready for their next book.", canWrite() ? [btn("Add a member", () => memberForm(), { icon: "plus" })] : [], "Members & students"), panel("Library members", table([{ label: "Member", render: row => titleCell(row.name, row.code) }, { label: "Year / group", key: "year_group" }, { label: "Status", render: statusBadge }, { label: "Actions", render: row => el("div", { className: "row-actions" }, canWrite() && btn("Edit", () => memberForm(row), { small: true, secondary: true }), canWrite() && btn("Remove", () => removeRecord("members", row, "member", row.name), { small: true, danger: true })) }], records, "Welcome your first member or student to the school library.")));
  }

  async function loansPage() {
    const records = items(await api("/loans"));
    return el("div", {}, heading("Every book has a journey", "Issue a book, record its return and see what your readers are enjoying.", canWrite() ? [btn("Issue a book", () => checkoutForm(), { icon: "checkout" })] : [], "Loans & returns"), panel("School loans", table([{ label: "Book", render: row => titleCell(nameOf(row), row.member_name) }, { label: "Issued", render: row => prettyDate(row.start_date) }, { label: "Due back", render: row => el("span", { className: row.state === "ITEM_ON_LOAN" && new Date(row.end_date) < new Date(new Date().toDateString()) ? "muted" : "" }, prettyDate(row.end_date)) }, { label: "Status", render: statusBadge }, { label: "Actions", render: row => el("div", { className: "row-actions" }, canWrite() && row.state === "ITEM_ON_LOAN" && btn("Return book", () => confirmAction("Return this book?", `${nameOf(row)} will be marked as returned and available for its next reader.`, "Return book", () => api(`/loans/${row.id}/return`, "POST", {}), "Book returned successfully."), { small: true, secondary: true, icon: "return" }), canWrite() && btn("Add fine", () => fineForm(row), { small: true, secondary: true })) }], records, "Issued books and returned loans will appear here.")));
  }

  async function reservationsPage() {
    const records = items(await api("/reservations"));
    return el("div", {}, heading("The next chapter is worth the wait", "Keep a place in line for the books your school’s readers want next.", canWrite() ? [btn("Make a reservation", () => reservationForm(), { icon: "plus" })] : [], "Reservations"), panel("School reservations", table([{ label: "Book", render: row => titleCell(nameOf(row), row.member_name) }, { label: "Reserved", render: row => prettyDate(row.created_at) }, { label: "Status", render: statusBadge }, { label: "Actions", render: row => canWrite() && row.status === "pending" && btn("Cancel reservation", () => confirmAction("Cancel this reservation?", `The reservation for ${nameOf(row)} will be cancelled.`, "Cancel reservation", () => api(`/reservations/${row.id}/cancel`, "POST", {}), "Reservation cancelled."), { small: true, secondary: true }) }], records, "Reservations made for this school’s members will appear here.")));
  }

  async function finesPage() {
    const records = items(await api("/fines"));
    return el("div", {}, heading("A clear record, a fresh start", "Manage library fines and record payments for your school’s members.", canWrite() ? [btn("Add a fine", () => fineForm(), { icon: "plus" })] : [], "Fines & payments"), el("div", { className: "info-note" }, "Payments recorded here are library records. No online payment is taken."), panel("School fines", table([{ label: "Member", render: row => titleCell(row.member_name || "Library member", row.reason) }, { label: "Fine", render: row => money(row.amount_cents) }, { label: "Outstanding", render: row => money(row.amount_cents - row.paid_cents) }, { label: "Status", render: statusBadge }, { label: "Actions", render: row => canWrite() && row.status !== "paid" && btn("Record payment", () => confirmAction("Record this fine as paid?", `Confirm that ${money(row.amount_cents - row.paid_cents)} has been received for this fine.`, "Record payment", () => api(`/fines/${row.id}/pay`, "POST", {}), "Payment recorded successfully."), { small: true, secondary: true }) }], records, "Your school’s fines and recorded payments will appear here.")));
  }

  async function transactionsPage() {
    const records = items(await api("/transactions"));
    const labels = { "member.created": "Member registered", "member.updated": "Member details updated", "member.archived": "Member removed", "copy.registered": "Book copy registered", "copy.updated": "Book copy updated", "copy.archived": "Book copy removed", "loan.checkout": "Book issued", "loan.return": "Book returned", "hold.created": "Reservation made", "hold.cancelled": "Reservation cancelled", "fine.created": "Fine added", "fine.paid": "Fine payment recorded" };
    return el("div", {}, heading("Your library’s story, recorded", "Follow the checkouts, returns, reservations and updates made in your school library.", [], "Transaction history"), panel("Recent activity", table([{ label: "Activity", render: row => titleCell(labels[row.action] || row.action?.replaceAll(".", " · "), row.book_title || row.member_name) }, { label: "Date", render: row => prettyDate(row.created_at) }, { label: "Member", render: row => row.member_name || "—" }, { label: "Amount", render: row => row.details?.amount_cents ? money(row.details.amount_cents) : "—" }], records, "Library activity will be recorded here as your school uses the collection.")));
  }

  async function schoolsPage() {
    const schools = items(await api("/admin/schools"));
    return el("div", {}, heading("Connected schools, individual libraries", "Manage school details and grant existing accounts an authorised school membership.", [btn("Add a school", () => schoolForm(), { icon: "plus" }), btn("Grant school access", () => membershipForm(schools), { secondary: true, icon: "users" })], "School administration"), panel("FOS schools", table([{ label: "School", render: row => titleCell(row.name, row.slug) }, { label: "Status", render: row => badge(row.active ? "Active" : "Inactive", row.active ? "" : "neutral") }, { label: "Actions", render: row => btn("Edit school", () => schoolForm(row), { small: true, secondary: true, icon: "edit" }) }], schools, "Add a school to grow the library network.")), el("div", { className: "info-note" }, "School access is granted to existing active accounts. Only the selected school and role are assigned. Platform administration remains a separate permission."));
  }

  async function auditPage() {
    const records = items(await api("/admin/audit"));
    return el("div", {}, heading("Accountable platform administration", "Review catalogue, school and membership administration activity.", [], "Activity & audit"), panel("Platform activity", table([{ label: "Action", render: row => el("span", { className: "audit-action" }, row.action) }, { label: "Date", render: row => prettyDate(row.occurred_at) }, { label: "Administrator", render: row => `Account ${row.actor_user_id}` }], records, "Platform administration activity will appear here.")));
  }

  const titles = { dashboard: "Overview", catalogue: "Shared catalogue", book: "Book details", inventory: "Book copies", members: "Members & students", loans: "Loans & returns", reservations: "Reservations", fines: "Fines & payments", transactions: "Transaction history", schools: "School administration", audit: "Activity & audit" };
  async function render() {
    if (!state.me) return;
    const sequence = ++state.sequence;
    const url = new URL(location.hash.slice(1) || "dashboard", "https://fos.local/");
    const [page, id] = url.pathname.slice(1).split("/"); state.page = page;
    const main = document.getElementById("page-content"); if (!main) return;
    const title = titles[page] || "Overview";
    document.title = `${title} · FOS Library Management System`;
    document.getElementById("breadcrumb-current").textContent = title;
    document.querySelectorAll(".nav-link").forEach(anchor => { const selected = anchor.dataset.page === (page === "book" ? "catalogue" : page); anchor.classList.toggle("active", selected); if (selected) anchor.setAttribute("aria-current", "page"); else anchor.removeAttribute("aria-current"); });
    main.replaceChildren(loading());
    try {
      const schoolPages = ["inventory", "members", "loans", "reservations", "fines", "transactions"];
      if (schoolPages.includes(page) && !school()) throw new Error("Select an authorised school to open its library.");
      if (["schools", "audit"].includes(page) && !isAdmin()) throw new Error("Platform administrator access is required.");
      const content = page === "catalogue" ? await cataloguePage(url.searchParams) : page === "book" ? await bookDetail(id) : await ({ dashboard, inventory: inventoryPage, members: membersPage, loans: loansPage, reservations: reservationsPage, fines: finesPage, transactions: transactionsPage, schools: schoolsPage, audit: auditPage }[page] || dashboard)();
      if (sequence === state.sequence) main.replaceChildren(content);
    } catch (error) {
      if (sequence !== state.sequence) return;
      if (error.status === 401) return showLogin(error.message);
      main.replaceChildren(el("section", { className: "error-panel", role: "alert" }, el("h2", {}, "We couldn’t open this page"), el("p", {}, error.message), btn("Try again", () => render(), { secondary: true })));
    }
  }

  function field(name, label, options = {}) {
    let input;
    if (options.choices) input = el("select", { name, id: `field-${name}`, required: options.required !== false }, options.placeholder && el("option", { value: "" }, options.placeholder), options.choices.map(choice => el("option", { value: choice.value }, choice.label)));
    else if (options.textarea) input = el("textarea", { name, id: `field-${name}`, maxlength: options.maxlength || 5000, required: options.required !== false });
    else input = el("input", { name, id: `field-${name}`, type: options.type || "text", maxlength: options.maxlength || 200, required: options.required !== false, min: options.min, max: options.max, step: options.step, autocomplete: options.autocomplete || "off" });
    if (options.value !== undefined && options.value !== null) input.value = options.value;
    return el("div", { className: "field" }, el("label", { for: `field-${name}` }, label), input, options.help && el("small", {}, options.help));
  }

  function checkbox(name, label, checked = true) {
    return el("div", { className: "check-field" }, el("input", { type: "checkbox", name, id: `field-${name}`, checked }), el("label", { for: `field-${name}` }, label));
  }

  function openForm(title, subtitle, fields, submitLabel, onSubmit, success) {
    const previousFocus = document.activeElement;
    const error = el("div", { className: "form-error", role: "alert", tabindex: "-1" });
    const submit = el("button", { type: "submit", className: "btn" }, submitLabel);
    const form = el("form", {}, el("div", { className: "dialog-head" }, el("div", {}, el("h2", { id: "dialog-title" }, title), subtitle && el("p", {}, subtitle)), el("button", { type: "button", className: "icon-button", "aria-label": "Close dialog", onclick: () => dialog.close() }, icon("close"))), el("div", { className: "dialog-body" }, fields, error), el("div", { className: "dialog-actions" }, btn("Cancel", () => dialog.close(), { secondary: true }), submit));
    form.addEventListener("submit", async event => {
      event.preventDefault(); error.textContent = "";
      if (!form.reportValidity()) return;
      const data = Object.fromEntries(new FormData(form));
      form.querySelectorAll("input[type=checkbox]").forEach(input => { data[input.name] = input.checked; });
      form.querySelectorAll("button").forEach(button => { button.disabled = true; });
      submit.textContent = "Saving…";
      try {
        await onSubmit(data);
        dialog.close(); notify(success || "Saved successfully."); render();
      } catch (failure) {
        error.textContent = failure.message; error.focus();
        form.querySelectorAll("button").forEach(button => { button.disabled = false; }); submit.textContent = submitLabel;
      }
    });
    dialog.replaceChildren(form);
    if (!dialog.open) dialog.showModal();
    dialog.addEventListener("close", () => { if (previousFocus?.isConnected) previousFocus.focus(); }, { once: true });
  }

  async function loadForm(action) { try { await action(); } catch (error) { notify(error.message, true); } }
  function confirmAction(title, copy, label, action, success) { openForm(title, copy, [el("p", { className: "muted small" }, "This change will be recorded in your library’s transaction history.")], label, action, success); }

  async function bookChoices() { return items(await api("/catalogue?page=1")).map(book => ({ value: book.id, label: `${book.title} — ${(book.authors || []).join(", ")}` })); }
  async function memberChoices() { return items(await api("/members")).filter(member => member.active !== false).map(member => ({ value: member.id, label: `${member.name} · ${member.code}` })); }

  function memberForm(member) {
    openForm(member ? "Edit library member" : "Welcome a new reader", school()?.name, [field("name", "Full name", { value: member?.name, maxlength: 200 }), field("code", "Member / student number", { value: member?.code, maxlength: 60 }), field("year_group", "Year or group", { value: member?.year_group || "", required: false, maxlength: 50 }), member && checkbox("active", "Active library member", member.active !== false)], member ? "Save member" : "Add member", data => api(member ? `/members/${member.id}` : "/members", member ? "PUT" : "POST", data), member ? "Member details updated." : "New member added to your school library.");
  }

  function copyForm(book) {
    loadForm(async () => {
      const books = book ? [{ value: book.id, label: book.title }] : await bookChoices();
      openForm("Register a school-owned copy", school()?.name, [field("book_id", "Book from the shared catalogue", { choices: books, value: book?.id, placeholder: book ? undefined : "Select a book", help: book ? undefined : "For more titles, find a book in the catalogue and choose Register a copy." }), field("barcode", "Copy barcode", { maxlength: 100, help: "Use the unique barcode on this school’s physical copy." })], "Register copy", data => api("/inventory", "POST", data), "Book copy added to your school’s collection.");
    });
  }

  function copyEditForm(copy) {
    openForm("Edit book copy", `${copy.barcode} · ${nameOf(copy)}`, [field("barcode", "Copy barcode", { value: copy.barcode, maxlength: 100 }), field("status", "Circulation status", { choices: [{ value: "CAN_CIRCULATE", label: "Available to circulate" }, { value: "FOR_REFERENCE_ONLY", label: "For reference only" }, { value: "MISSING", label: "Missing" }, { value: "IN_BINDING", label: "In binding" }, { value: "SCANNING", label: "Scanning" }], value: copy.status })], "Save copy", data => api(`/inventory/${copy.id}`, "PUT", data), "Book copy updated.");
  }

  function removeRecord(resource, record, label, name) {
    confirmAction(`Remove this ${label}?`, `${name} will be removed from the active school library. Records with circulation history or other references cannot be removed.`, `Remove ${label}`, () => api(`/${resource}/${record.id}`, "DELETE", {}), `${label === "member" ? "Member" : "Book copy"} removed from the active library.`);
  }

  function checkoutForm() {
    loadForm(async () => {
      const [inventory, members] = await Promise.all([api("/inventory"), memberChoices()]);
      const available = items(inventory).filter(copy => !copy.checked_out && copy.status === "CAN_CIRCULATE");
      if (!available.length || !members.length) return notify(!available.length ? "Register an available book copy before issuing a loan." : "Add an active library member before issuing a loan.", true);
      const due = new Date(); due.setDate(due.getDate() + 14);
      openForm("Issue a book", school()?.name, [field("copy_id", "Available book copy", { choices: available.map(copy => ({ value: copy.id, label: `${nameOf(copy)} · ${copy.barcode}` })), placeholder: "Select a copy" }), field("member_id", "Library member", { choices: members, placeholder: "Select a member" }), field("due_date", "Due back", { type: "date", value: due.toISOString().slice(0, 10), min: new Date().toISOString().slice(0, 10) })], "Issue book", data => api("/loans", "POST", data), "Book issued successfully. Happy reading!");
    });
  }

  function reservationForm(book) {
    loadForm(async () => {
      const [books, members] = await Promise.all([book ? Promise.resolve([{ value: book.id, label: book.title }]) : bookChoices(), memberChoices()]);
      if (!members.length) return notify("Add an active member before making a reservation.", true);
      openForm("Reserve the next chapter", school()?.name, [field("book_id", "Book", { choices: books, value: book?.id, placeholder: book ? undefined : "Select a book" }), field("member_id", "Library member", { choices: members, placeholder: "Select a member" })], "Make reservation", data => api("/reservations", "POST", data), "Reservation added for your school’s reader.");
    });
  }

  function fineForm(loan) {
    loadForm(async () => {
      const loans = loan ? [loan] : items(await api("/loans"));
      if (!loans.length) return notify("A fine must be linked to an existing school loan.", true);
      openForm("Add a library fine", school()?.name, [field("loan_id", "Related loan", { choices: loans.map(item => ({ value: item.id, label: `${nameOf(item)} · ${item.member_name || "Library member"}` })), value: loan?.id, placeholder: loan ? undefined : "Select a loan" }), field("amount", "Fine amount (USD)", { type: "number", min: ".01", max: "100000", step: ".01", help: "Enter the amount in dollars, for example 2.50." }), field("reason", "Reason", { maxlength: 200 })], "Add fine", data => api("/fines", "POST", { loan_id: data.loan_id, amount_cents: Math.round(Number(data.amount) * 100), reason: data.reason }), "Fine recorded for this school loan.");
    });
  }

  function schoolForm(schoolRecord) {
    openForm(schoolRecord ? "Edit school" : "Add a school", "FOS platform administration", [field("name", "School name", { value: schoolRecord?.name }), field("slug", "School short name", { value: schoolRecord?.slug, maxlength: 63, help: "Use lowercase letters, numbers and hyphens, for example alpha-school." }), checkbox("active", "Active school", schoolRecord?.active !== false)], "Save school", data => api("/admin/schools", "POST", { ...data, id: schoolRecord?.id }), "School details saved.");
  }

  function membershipForm(schools) {
    openForm("Grant school access", "Choose an existing active account and its school role.", [field("email", "Account email address", { type: "email", autocomplete: "email", maxlength: 254 }), field("tenant_id", "School", { choices: schools.filter(item => item.active).map(item => ({ value: item.id, label: item.name })), placeholder: "Select a school" }), field("role", "School role", { choices: [{ value: "librarian", label: "Librarian — manage the school library" }, { value: "reader", label: "Reader — view the school library" }] })], "Grant school access", data => api("/admin/memberships", "POST", data), "School membership granted.");
  }

  function catalogueForm(book) {
    openForm(book ? "Edit shared catalogue book" : "Add a shared catalogue book", "Platform catalogue changes are recorded in the activity log.", [field("title", "Book title", { value: book?.title, maxlength: 1000 }), field("authors", "Author names", { value: (book?.authors || []).join(", "), help: "Separate multiple authors with a comma.", maxlength: 2000 }), field("isbn13", "ISBN-13", { value: (book?.isbn || []).find(value => value.replaceAll("-", "").length === 13) || (book?.isbn || [])[0], maxlength: 25 }), el("div", { className: "field-row" }, field("publisher", "Publisher", { value: book?.publisher || "", required: false }), field("publication_year", "Publication year", { value: book?.publication_year || "", required: false, maxlength: 40 })), field("edition", "Edition", { value: book?.edition || "", required: false }), field("description", "Description", { value: book?.description || "", textarea: true, required: false })], "Save catalogue book", data => api("/admin/catalogue", "POST", { ...data, id: book?.id, authors: data.authors.split(",").map(value => value.trim()).filter(Boolean) }), "Shared catalogue book saved.");
  }

  window.addEventListener("hashchange", () => { dialog.close(); render(); });
  window.addEventListener("keydown", event => { if (event.key === "Escape" && !dialog.open) document.querySelector(".sidebar.is-open .sidebar-close")?.click(); });
  dialog.addEventListener("click", event => { if (event.target === dialog) { const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); } });
  async function start() {
    try { state.me = await api("/me"); mountShell(); }
    catch (error) { showLogin(error.status === 401 ? undefined : error.message); }
  }
  start();
})();
