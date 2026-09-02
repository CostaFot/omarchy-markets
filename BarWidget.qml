pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Bar widget for Markets: the favorites strip as "SYM $price ▲ +x%" runs,
// values tinted by direction. Click opens the watchlist panel (Panel.qml,
// which owns the data); middle click refreshes. The strip trims itself from
// the end to fit the room this bar can give it, down to a lone glyph.
BarWidget {
  id: root
  moduleName: "costafot.markets"

  // nf-fa-line_chart: the class glyph, shown alone while loading, on
  // vertical bars, and when the bar has no room for a single entry.
  readonly property string glyph: ""

  // Untyped on purpose: naming the type would collide with qs.Ui's Panel base.
  readonly property var marketPanel: panelLoader.item
  readonly property var store: marketPanel ? marketPanel.store : null

  // ---- Panel shape contract for shell.summon/hide/toggle routing ---------
  readonly property bool opened: marketPanel ? marketPanel.opened === true : false
  function open() { if (marketPanel) marketPanel.open() }
  function close() { if (marketPanel) marketPanel.close() }
  function togglePanel() { if (marketPanel) marketPanel.toggle() }
  readonly property bool popoutSwitchClosing: marketPanel ? marketPanel.popoutSwitchClosing === true : false
  function closeForPopoutSwitch() { if (marketPanel) marketPanel.closeForPopoutSwitch() }
  function refresh() { if (marketPanel) marketPanel.refresh() }

  function injectPanel() {
    var target = marketPanel
    if (!target) return
    target.bar = root.bar
    target.settings = root.settings
    target.anchorItem = button
    target.hostWidget = root
  }

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  //   omarchy-shell costafot.markets toggle
  //   omarchy-shell costafot.markets refresh
  //   omarchy-shell costafot.markets page watchlist      # hub search watchlist favorites
  //   omarchy-shell costafot.markets add DOGE crypto
  //   omarchy-shell costafot.markets favorite DOGE       # toggles; DOGE:crypto for a new symbol
  // `refresh` reaches every bar instance (one per monitor), not just the one
  // that owns the IPC target. The mutations go through the helper like any
  // panel action, so every instance sees them on its next poll.
  IpcHandler {
    target: "costafot.markets"
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.togglePanel() }
    function refresh(): void { root.broadcast("refresh") }
    function page(name: string): void { if (root.marketPanel) root.marketPanel.showPage(name) }
    function add(symbol: string, category: string): void { if (root.marketPanel) root.marketPanel.addSymbol(symbol, category) }
    function favorite(symbol: string): void { if (root.marketPanel) root.marketPanel.favoriteSymbol(symbol) }
    function status(): string { return root.statusJson() }
  }

  //   omarchy-shell costafot.markets status | jq
  // What this bar instance is showing, for a terminal. The helper's own
  // `bin/markets status` covers the data side (providers, state dir).
  function statusJson() {
    var s = root.store
    var doc = {
      opened: root.opened,
      page: root.marketPanel ? root.marketPanel.page : "",
      has_data: root.hasData,
      busy: s ? s.busy : false,
      stale: s ? s.stale : false,
      rate_limited: s ? s.rateLimited : false,
      banner: s ? s.rateLimitBanner : false,
      generated_at: s ? s.generatedAt : 0,
      error: s ? s.lastError : "",
      error_code: s ? s.lastErrorCode : "",
      strip: root.fullText,
      shown: root.fitCount,
      entries: root.entries.length,
      strip_max_width: root.stripMaxWidth,
      extras: s ? s.extras : [],
      chart: root.marketPanel ? root.marketPanel.chartStatus() : null
    }
    return JSON.stringify(doc)
  }

  // ---- Strip model ---------------------------------------------------------
  readonly property var entries: store ? store.strip : []
  readonly property bool hasData: store ? store.hasData : false
  // The pause glyph: the newest run failed, the helper's rate-limit latch
  // is up, or any entry shown is a kept last-good price.
  readonly property bool stripStale: {
    if (!store) return false
    if (store.stale || store.rateLimited) return true
    for (var i = 0; i < entries.length; i++) if (entries[i].stale === true) return true
    return false
  }
  // barForeground (not foreground) so the strip follows the bar's transparent-mode colour.
  readonly property color baseFg: root.bar ? root.bar.barForeground : Color.foreground
  readonly property color dimFg: Qt.darker(baseFg, 1.55)

  function entryColor(entry) {
    if (!store || entry.valid === false) return dimFg
    return store.dirColor(entry.dir, baseFg)
  }

  // ---- Width-aware degradation, per bar instance ---------------------------
  // The strip must never paint over neighbouring sections. From this
  // window's real geometry, compute the width the bar can give this widget,
  // then drop entries from the end down to the glyph as the floor. The
  // geometry model (sections, centre anchor, flanks) follows Bar.qml.
  readonly property real stripMaxWidth: {
    if (!root.bar || root.vertical) return -1
    var win = root.QsWindow.window
    if (!win || !(win.width > 0)) return -1
    var barHost = root.bar
    if (typeof barHost.slotWindow !== "function" || typeof barHost.sameWindow !== "function") return -1
    var slots = barHost.moduleSlots || []
    var W = win.width
    var margin = Style.space(8)
    var safety = Style.space(12)

    var entriesList = typeof barHost.layoutEntries === "function" ? barHost.layoutEntries("center") : []
    var anchorName = String(barHost.centerAnchor || "")
    var anchorIdx = -1
    var myIdx = -1
    for (var e = 0; e < entriesList.length; e++) {
      var id = typeof barHost.entryId === "function" ? String(barHost.entryId(entriesList[e])) : ""
      if (id === anchorName) anchorIdx = e
      if (id === root.moduleName) myIdx = e
    }
    var anchored = anchorIdx !== -1 && myIdx !== -1

    var left = 0, right = 0, centerOther = 0, anchorW = 0, flankOther = 0
    for (var i = 0; i < slots.length; i++) {
      var s = slots[i]
      if (!s || s.activeItem === root) continue
      if (!barHost.sameWindow(barHost.slotWindow(s), win)) continue
      var w = s.width || 0
      if (s.region === "left") left += w
      else if (s.region === "right") right += w
      else if (s.region === "center") {
        if (!anchored) { centerOther += w; continue }
        var idx = typeof barHost.entryIndex === "function"
          ? barHost.entryIndex(entriesList, String(s.moduleName)) : -1
        if (idx === anchorIdx) anchorW = w
        else if (myIdx > anchorIdx && idx > anchorIdx) flankOther += w
        else if (myIdx < anchorIdx && idx !== -1 && idx < anchorIdx) flankOther += w
      }
    }

    var available
    if (anchored && myIdx > anchorIdx) available = W / 2 - anchorW / 2 - (right + margin) - flankOther - safety
    else if (anchored && myIdx < anchorIdx) available = W / 2 - anchorW / 2 - (left + margin) - flankOther - safety
    else if (anchored) available = W - 2 * (Math.max(left, right) + margin) - safety
    else available = W - 2 * (Math.max(left, right) + margin) - centerOther - safety
    return Math.max(0, available)
  }

  FontMetrics {
    id: barFm
    font.family: root.bar ? root.bar.fontFamily : Style.font.family
    font.pixelSize: Style.font.body
  }

  readonly property string separator: "  ·  "

  // How many leading entries fit (with the "…" marker when trimmed). 0 = glyph only.
  readonly property int fitCount: {
    var n = entries.length
    if (n === 0 || root.vertical) return 0
    var max = stripMaxWidth
    if (max < 0) return n
    void barFm.font.pixelSize
    var sepW = barFm.advanceWidth(separator)
    var markerW = sepW + barFm.advanceWidth("…")
    var pad = Style.spaceReal(8.5) * 2
    var staleW = root.stripStale ? barFm.advanceWidth("  ") : 0
    var used = pad + staleW
    var fit = 0
    for (var i = 0; i < n; i++) {
      var e = entries[i]
      var w = barFm.advanceWidth(e.label + " " + e.value_text)
      var need = used + (i > 0 ? sepW : 0) + w
      if (need + (i < n - 1 ? markerW : 0) > max) break
      used = need
      fit = i + 1
    }
    return fit
  }

  readonly property bool truncated: fitCount < entries.length
  readonly property bool glyphOnly: root.vertical || entries.length === 0 || fitCount === 0
  // No room even for the glyph: vanish instead of piling onto a neighbour.
  readonly property bool hiddenByWidth: !root.vertical && stripMaxWidth >= 0
    && stripMaxWidth < barFm.advanceWidth(glyph) + Style.spaceReal(8.5) * 2

  // Coloured text runs: label in the plain foreground, value by direction.
  readonly property var pieces: {
    var out = []
    var shown = Math.min(fitCount, entries.length)
    for (var i = 0; i < shown; i++) {
      var e = entries[i]
      var base = e.valid === false ? dimFg : baseFg
      if (i > 0) out.push({ text: separator, color: Util.alpha(baseFg, 0.35) })
      out.push({ text: e.label + " ", color: base })
      out.push({ text: e.value_text, color: entryColor(e) })
    }
    if (shown > 0 && truncated) out.push({ text: separator + "…", color: Util.alpha(baseFg, 0.35) })
    // nf-fa-pause: the prices keep their colours, staleness gets its own mark.
    if (shown > 0 && root.stripStale) out.push({ text: "  ", color: dimFg })
    return out
  }

  // Plain concatenation: sizes the WidgetButton and is the vertical fallback.
  readonly property string plainText: {
    if (hiddenByWidth) return ""
    if (glyphOnly) return glyph
    var s = ""
    for (var i = 0; i < pieces.length; i++) s += pieces[i].text
    return s
  }

  readonly property string fullText: {
    var s = ""
    for (var i = 0; i < entries.length; i++) {
      if (i > 0) s += separator
      s += entries[i].label + " " + entries[i].value_text
    }
    return s
  }

  // Glyph tint when collapsed: the shared direction of every entry, else plain.
  readonly property color glyphColor: {
    if (entries.length === 0 || !store) return baseFg
    var dir = entries[0].dir
    for (var i = 1; i < entries.length; i++) if (entries[i].dir !== dir) return baseFg
    return store.dirColor(dir, baseFg)
  }

  // The bar's open-panel mark follows what is painted, not the slot.
  readonly property real markExtent: Math.round(contentRow.implicitWidth)
  readonly property real openPanelIndicatorWidth: root.glyphOnly ? button.labelWidth : markExtent

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.plainText
    labelVisible: root.glyphOnly
    fixedWidth: root.glyphOnly || root.vertical ? -1 : contentRow.implicitWidth + button.scaledHorizontalMargin * 2
    foreground: root.glyphOnly ? root.glyphColor : root.baseFg
    // Dim only while there is nothing to show; a failed fetch behind cached
    // prices is marked by the pause glyph and named in the panel.
    dimmed: !root.hasData
    tooltipText: root.glyphOnly && root.fullText !== "" ? root.fullText : ""

    onPressed: function(b) {
      if (b === Qt.MiddleButton) root.refresh()
      else root.togglePanel()
    }

    Row {
      id: contentRow
      visible: !root.glyphOnly
      x: Math.round((parent.width - root.markExtent) / 2)
      anchors.verticalCenter: parent.verticalCenter
      spacing: 0

      Repeater {
        model: root.pieces

        Text {
          required property var modelData
          text: modelData.text
          textFormat: Text.PlainText
          color: modelData.color
          font.family: root.bar ? root.bar.fontFamily : Style.font.family
          font.pixelSize: Style.font.body
          renderType: Text.NativeRendering
          verticalAlignment: Text.AlignVCenter

          Behavior on color {
            enabled: !root.bar || root.bar.foregroundAnimationEnabled
            ColorAnimation { duration: 160 }
          }
        }
      }
    }
  }
}
