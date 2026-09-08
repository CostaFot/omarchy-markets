pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons

// The Markets pages as their own window: a FloatingWindow, a Wayland
// toplevel that Hyprland tiles or floats like any app, resizable, with a
// title a window rule can match, and ordinary keyboard focus. The shell's
// panel loader creates it once at start (`keepLoaded`) and hands it
// `shell`; `shell summon costafot.markets '{"page":"portfolio"}'` (or the
// `window` verb below) calls `open` with the payload, `shell hide` calls
// `close`, and a window the user closes (Escape on the hub, the close
// button) tells the shell so its open-panel map stays right (the dev
// gallery's pattern). It does not replace the bar popup, which stays the
// one-key surface unless the `openAsWindow` setting says otherwise.
//
// This is also where the plugin's IPC target lives: one instance per shell,
// where a bar widget exists once per monitor and only one of them could
// have claimed the target. With no service kind, the popup verbs go to the
// bar's own `summonBarWidget`/`hideBarWidget` (the widget on the focused
// monitor, what the shell's summon did for this plugin before it had a
// `panel` kind: with one, the shell's summon and hide own the window).
// The settings are the bar widget's shell.json entry, read off the widget
// the bar resolves (`findPanelWidget`); with none placed the window runs
// on the defaults and a Save keeps them for the session.
Item {
  id: root

  // Injected by the shell's panel loader.
  property var shell: null
  property var manifest: null
  property string omarchyPath: ""

  readonly property string pluginId: "costafot.markets"
  readonly property bool opened: window.visible
  readonly property string page: pages.page
  // The window's own idea of its size, for `status` (a rule or a drag
  // resizes it; the content follows the surface).
  readonly property var windowSize: ({ width: window.width, height: window.height, content_width: pages.width, content_height: pages.height })
  property bool closingFromHost: false

  readonly property var pageNames: ["hub", "search", "watchlist", "favorites", "portfolio", "sources", "settings"]

  // ---- The bar widget ---------------------------------------------------------
  // The bar widget on the focused monitor, looked up when something needs
  // it (the bar mounts after the panel loader, and widgets come and go with
  // the layout). Its `settings` is what the pages here read and write.
  property var hostWidget: null

  readonly property var barApi: shell && shell.bar ? shell.bar : null

  function resolveHost() {
    var found = barApi && typeof barApi.findPanelWidget === "function" ? barApi.findPanelWidget(pluginId) : null
    if (found !== hostWidget) hostWidget = found
    return hostWidget
  }

  readonly property bool openAsWindow: hostWidget && hostWidget.settings ? hostWidget.settings.openAsWindow === true : false

  onShellChanged: Qt.callLater(resolveHost)
  Component.onCompleted: Qt.callLater(resolveHost)

  // ---- Open and close (the shell's panel contract) -----------------------------
  // The payload may name a page: {"page": "portfolio"}. Unknown names open
  // the hub. Open already: the page moves and the keys come back here.
  function open(payloadJson) {
    resolveHost()
    var page = ""
    if (payloadJson) {
      try {
        var parsed = JSON.parse(String(payloadJson))
        if (parsed && typeof parsed.page === "string") page = parsed.page
      } catch (e) { /* no page */ }
    }
    closingFromHost = false
    if (page !== "") pages.showPage(page)
    window.visible = true
    Qt.callLater(function() { if (window.visible) pages.focusForPage() })
  }

  // Host-initiated (`shell hide`): the shell already knows.
  function close() {
    closingFromHost = true
    window.visible = false
    closingFromHost = false
  }

  function showPage(name) { pages.showPage(String(name)) }

  // User-initiated (Escape on the hub, the window's close button): through
  // the shell, so `toggle` works on the next call.
  function requestClose() {
    if (shell && typeof shell.hide === "function") shell.hide(pluginId)
    else window.visible = false
  }

  // ---- The window verbs ------------------------------------------------------
  function isWindowOpen() {
    return window.visible
  }

  function openWindow(page) {
    if (!shell || typeof shell.summon !== "function") return "no window: the shell cannot summon it"
    var payload = page ? JSON.stringify({ page: String(page) }) : ""
    return shell.summon(pluginId, payload) ? "opened" : "no window: the plugin is not enabled as a panel"
  }

  function closeWindow() {
    if (!shell || typeof shell.hide !== "function") return "no window: the shell cannot hide it"
    shell.hide(pluginId)
    return "closed"
  }

  function toggleWindow() { return isWindowOpen() ? closeWindow() : openWindow("") }

  function windowVerb(mode) {
    var m = String(mode || "").trim().toLowerCase()
    if (m === "" || m === "toggle") return toggleWindow()
    if (m === "open" || m === "show") return openWindow("")
    if (m === "close" || m === "hide") return closeWindow()
    if (pageNames.indexOf(m) !== -1) return openWindow(m)
    return "window takes open, close, toggle or a page: " + pageNames.join(" ")
  }

  // ---- The popup verbs -------------------------------------------------------
  // The bar's summonBarWidget/hideBarWidget pick the widget on the focused
  // monitor. With no bar widget placed there is nothing to open.
  readonly property var barSummoner: barApi && typeof barApi.summonBarWidget === "function"
    && typeof barApi.hideBarWidget === "function" ? barApi : null

  function isPanelOpen() {
    return barApi && typeof barApi.isBarWidgetOpen === "function" ? barApi.isBarWidgetOpen(pluginId) === true : false
  }

  function openPanel() {
    if (!barSummoner) return "no bar"
    return barSummoner.summonBarWidget(pluginId) ? "opened" : "no bar widget placed"
  }

  function closePanel() {
    if (!barSummoner) return "no bar"
    return barSummoner.hideBarWidget(pluginId) ? "closed" : "no bar widget placed"
  }

  function togglePanel() { return isPanelOpen() ? closePanel() : openPanel() }

  // `page NAME` on the popup: the widget on the focused monitor opens on
  // that page (its own pending stack covers the closed case).
  function showPanelPage(name) {
    var host = resolveHost()
    if (!host || typeof host.showPage !== "function") return "no bar widget placed"
    host.showPage(String(name))
    return "opened"
  }

  // ---- Which surface the one-key verbs open ---------------------------------
  // With the `openAsWindow` setting on, `open`, `close`, `toggle` and `page`
  // (and the glyph's left click) address the window and the popup is the
  // other surface (the glyph's right click); off, the popup is the main one
  // and the window the other. The `window` verb is explicit either way.
  function openMain() { resolveHost(); return openAsWindow ? openWindow("") : openPanel() }
  function closeMain() { resolveHost(); return openAsWindow ? closeWindow() : closePanel() }
  function toggleMain() { resolveHost(); return openAsWindow ? toggleWindow() : togglePanel() }
  function showPageMain(name) {
    resolveHost()
    if (!openAsWindow) return showPanelPage(name)
    var m = String(name || "").trim().toLowerCase()
    return openWindow(pageNames.indexOf(m) !== -1 ? m : "hub")
  }

  // ---- The mutations and the rest --------------------------------------------
  // A membership change goes through the helper like any panel action, so
  // every store (one per bar instance, one here) sees it on its next poll;
  // the answer lands in whichever store ran it.
  function addSymbol(symbol, category) {
    var host = resolveHost()
    if (host && typeof host.addSymbol === "function") host.addSymbol(symbol, category)
    else pages.addSymbol(symbol, category)
    return "ok"
  }

  function favoriteSymbol(spec) {
    var host = resolveHost()
    if (host && typeof host.favoriteSymbol === "function") host.favoriteSymbol(spec)
    else pages.favoriteSymbol(spec)
    return "ok"
  }

  // Every bar instance (one per monitor) and the window when it is up.
  function refreshAll() {
    var items = barApi && typeof barApi.moduleWidgets === "function" ? barApi.moduleWidgets(pluginId) : []
    var n = 0
    for (var i = 0; i < items.length; i++) {
      if (items[i] && typeof items[i].refresh === "function") { items[i].refresh(); n++ }
    }
    if (window.visible) { pages.refresh(); n++ }
    return n > 0 ? "refreshing" : "nothing to refresh"
  }

  //   omarchy-shell costafot.markets status | jq
  // The focused monitor's bar widget (what its strip shows, its popup, its
  // chart) plus this window.
  function statusJson() {
    var host = resolveHost()
    var doc = {}
    if (host && typeof host.statusJson === "function") {
      try { doc = JSON.parse(host.statusJson()) } catch (e) { doc = {} }
    }
    doc.open_as_window = openAsWindow
    doc.window = { opened: window.visible, page: window.visible ? pages.page : "",
                   size: window.visible ? windowSize : null }
    return JSON.stringify(doc)
  }

  //   omarchy-shell costafot.markets toggle              # the popup, or the window with openAsWindow on
  //   omarchy-shell costafot.markets window toggle       # the window; open, close, or a page name
  //   omarchy-shell costafot.markets page watchlist      # hub search watchlist favorites portfolio sources settings
  //   omarchy-shell costafot.markets add DOGE crypto
  //   omarchy-shell costafot.markets favorite DOGE       # toggles; DOGE:crypto for a new symbol
  //   omarchy-shell costafot.markets refresh             # every bar instance and the window
  IpcHandler {
    target: "costafot.markets"
    function open(): string { return root.openMain() }
    function show(): string { return root.openMain() }
    function close(): string { return root.closeMain() }
    function hide(): string { return root.closeMain() }
    function toggle(): string { return root.toggleMain() }
    function page(name: string): string { return root.showPageMain(name) }
    function window(mode: string): string { return root.windowVerb(mode) }
    function refresh(): string { return root.refreshAll() }
    function add(symbol: string, category: string): string { return root.addSymbol(symbol, category) }
    function favorite(symbol: string): string { return root.favoriteSymbol(symbol) }
    function status(): string { return root.statusJson() }
  }

  FloatingWindow {
    id: window
    title: "Markets"
    color: Color.background
    implicitWidth: Style.space(400)
    implicitHeight: Style.space(760)
    minimumSize: Qt.size(Style.space(320), Style.space(420))
    // keepLoaded would show it at shell start otherwise.
    visible: false

    onVisibleChanged: {
      if (!visible && !root.closingFromHost) root.requestClose()
    }

    Pages {
      id: pages
      anchors.fill: parent
      anchors.margins: Style.spacing.popupPadding
      settings: root.hostWidget && root.hostWidget.settings ? root.hostWidget.settings : ({})
      shell: root.shell
      hostWidget: root.hostWidget
      opened: window.visible
      onCloseRequested: root.requestClose()
      onOpenRequested: window.visible = true
    }
  }
}
