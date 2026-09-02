pragma ComponentBehavior: Bound
import QtQuick
import qs.Commons
import qs.Ui

// Popup for the Markets bar widget: a hub that funnels into Search,
// Watchlist and Favorites, and a detail page per instrument where
// membership is managed. Pages live on a stack; Escape and Backspace walk
// back, Escape on the hub closes. The panel owns the Store, so the strip in
// the bar and the rows in here are the same document.
//
// Page renderers build one flat `rows` array and a Repeater paints it; the
// only widget outside the list is the filter field the list pages start
// with. While that field has focus the key catcher is blocked and the field
// forwards Up/Down/Enter/Escape/Tab itself.
Panel {
  id: root
  moduleName: "costafot.markets"

  property var anchorItem: null
  property var hostWidget: null
  // The bar tracks the widget mounted in its slot (BarWidget.qml), so the
  // popout coordinator and panel switching must identify us by that widget.
  readonly property var barIdentity: hostWidget || root

  readonly property color contentForeground: bar ? bar.foreground : Color.foreground
  readonly property string contentFontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property color mutedForeground: Qt.darker(contentForeground, 1.4)
  readonly property color urgentForeground: bar ? bar.urgent : Color.urgent

  readonly property string pluginDir: {
    var dir = Qt.resolvedUrl(".").toString()
    return dir.replace(/^file:\/\//, "").replace(/\/$/, "")
  }

  readonly property Store store: Store {
    pluginDir: root.pluginDir
    settings: root.settings
    extras: root.detailExtras
  }

  function refresh() {
    store.refresh(true)
    if (page === "detail") loadChart(chartRange, true)
  }

  // ---- Navigation ---------------------------------------------------------
  // Each entry: { page, ...args, cursor, query } where cursor and query are
  // saved on push and restored on pop, so backing out of a detail page lands
  // on the row that opened it with the filter still typed.
  property var stack: [{ page: "hub" }]
  property var pendingStack: null
  readonly property var current: stack[stack.length - 1]
  readonly property string page: current.page
  readonly property bool isHub: stack.length === 1
  readonly property bool hasField: page === "search" || page === "watchlist" || page === "favorites"

  readonly property var pageTitles: ({
    hub: "Markets", search: "Search", watchlist: "Watchlist", favorites: "Favorites",
    portfolio: "Portfolio", news: "News", sources: "Data sources", settings: "Settings"
  })

  function push(entry) {
    var top = Object.assign({}, current, { cursor: selectedIndex, query: filterField.text })
    stack = stack.slice(0, -1).concat([top, entry])
    enterPage(entry)
  }

  function pop() {
    if (stack.length <= 1) { root.close(); return }
    var entry = stack[stack.length - 2]
    stack = stack.slice(0, -1)
    enterPage(entry)
  }

  function goHome() {
    stack = [{ page: "hub" }]
    enterPage(stack[0])
  }

  // IPC `page NAME`: open straight onto a page, with the hub under it.
  function showPage(name) {
    var target = pageTitles[name] !== undefined ? name : "hub"
    var next = target === "hub" ? [{ page: "hub" }] : [{ page: "hub" }, { page: target }]
    if (opened) {
      stack = next
      enterPage(next[next.length - 1])
    } else {
      pendingStack = next
      root.open()
    }
  }

  function openDetail(symbol, name, category) {
    push({ page: "detail", symbol: symbol, name: name || symbol, category: category || "stock" })
  }

  function enterPage(entry) {
    listScroll.contentY = 0
    filterField.text = entry.query || ""
    if (entry.page === "detail") {
      if (!instrumentFor(entry.symbol)) store.refresh(false)
      loadChart(chartRange, false)
    }
    Qt.callLater(function() {
      var wanted = entry.cursor
      selectedIndex = (wanted !== undefined && root.isCursorRow(root.rows[wanted])) ? wanted : root.firstCursorIndex()
      root.focusForPage()
      root.ensureCursorVisible()
    })
  }

  function focusForPage() {
    if (hasField) filterField.forceActiveFocus()
    else keyCatcher.forceActiveFocus()
  }

  // The untracked symbols on the stack, priced alongside the watchlist while
  // their detail page is up.
  readonly property var detailExtras: {
    var out = []
    var tracked = store.instruments
    for (var i = 0; i < stack.length; i++) {
      var e = stack[i]
      if (e.page !== "detail") continue
      var known = false
      for (var t = 0; t < tracked.length; t++) if (tracked[t].symbol === e.symbol) { known = true; break }
      if (!known) out.push(e.symbol + ":" + e.category)
    }
    return out
  }

  function instrumentFor(symbol) {
    var list = store.instruments
    for (var i = 0; i < list.length; i++) if (list[i].symbol === symbol) return list[i]
    return null
  }

  // ---- Search state -------------------------------------------------------
  // Enter-only: typing changes the query, only the action row (or Enter on
  // it) runs the one helper call. Results show while the field still says
  // the query they belong to.
  property string searchedQuery: ""
  property var searchResults: null
  property var searchAttribution: []
  property bool searching: false
  readonly property string filterText: filterField.text
  readonly property string query: filterText.trim()

  function runSearch() {
    var q = query
    if (q === "" || searching) return
    searching = true
    store.run(["search", q], function(doc) {
      root.searching = false
      if (!doc || !Array.isArray(doc.results)) {
        root.searchResults = null
        root.searchedQuery = ""
        return
      }
      root.searchResults = doc.results
      root.searchedQuery = q
      root.searchAttribution = Array.isArray(doc.attribution) ? doc.attribution : []
      Qt.callLater(function() {
        root.selectedIndex = root.firstIndexOfType("instrument")
        if (root.selectedIndex === -1) root.selectedIndex = root.firstCursorIndex()
        root.ensureCursorVisible()
      })
    })
  }

  // ---- Chart --------------------------------------------------------------
  // Port of SymbolChartForm: the range is sticky while the panel is up, a
  // prior chart stays painted while another range loads (the "Loading…"
  // card only ever shows before the first chart of a symbol), and a
  // generation counter drops the answer to a superseded request. Series
  // are kept per symbol and range for five minutes, the helper's own TTL,
  // so a tab revisited inside that window costs no process at all.
  readonly property var chartRanges: ["1D", "1W", "1M", "1Y", "5Y"]
  property string chartRange: "1D"
  property var chartSeries: null
  property var chartCache: ({})
  property bool chartLoading: false
  property string chartError: ""
  property int chartGeneration: 0
  readonly property int chartTtlMs: 5 * 60 * 1000

  function chartKey(symbol, range) { return symbol + "|" + range }

  function loadChart(range, force) {
    if (page !== "detail") return
    var e = current
    var sym = e.symbol
    var inst = instrumentFor(sym)
    var cat = inst ? inst.category : e.category
    chartRange = range
    var key = chartKey(sym, range)
    var hit = chartCache[key]
    if (hit) chartSeries = hit.series
    else if (!chartSeries || chartSeries.symbol !== sym) chartSeries = null
    chartError = ""
    if (hit && !force && Date.now() - hit.at < chartTtlMs) { chartLoading = false; return }
    var gen = ++chartGeneration
    chartLoading = true
    store.run(["candles", sym + ":" + cat, range], function(doc) {
      if (gen !== root.chartGeneration) return
      root.chartLoading = false
      var series = doc && doc.series ? doc.series : null
      if (!series) {
        root.chartError = doc && doc.error && doc.error.message ? doc.error.message : "No answer from the helper"
        return
      }
      if (series.valid) {
        var cache = Object.assign({}, root.chartCache)
        cache[key] = { series: series, at: Date.now() }
        root.chartCache = cache
        root.chartSeries = series
      } else if (!root.chartSeries || root.chartSeries.symbol !== sym) {
        root.chartSeries = series
      } else {
        // Keep the chart that was fine; say why this range is not.
        root.chartError = series.message || "No chart data for this range"
      }
    })
  }

  function setRange(range) {
    if (chartRanges.indexOf(range) === -1 || page !== "detail") return
    loadChart(range, false)
  }

  function stepRange(delta) {
    var i = chartRanges.indexOf(chartRange)
    if (i === -1) i = 0
    setRange(chartRanges[(i + delta + chartRanges.length) % chartRanges.length])
  }

  // For the `status` IPC.
  function chartStatus() {
    return {
      range: chartRange,
      symbol: chartSeries ? chartSeries.symbol : "",
      points: chartSeries && Array.isArray(chartSeries.points) ? chartSeries.points.length : 0,
      valid: chartSeries ? chartSeries.valid === true : false,
      loading: chartLoading,
      error: chartError,
      cached: Object.keys(chartCache).length
    }
  }

  // ---- Membership ---------------------------------------------------------
  // Mutations stay on the page; the helper answers with the new membership
  // and strip, the store merges them, the rows re-render in place. A short
  // notice says what happened (a silent flip left people unsure).
  property string notice: ""
  property bool noticeUrgent: false

  function showNotice(text, urgent) {
    notice = text
    noticeUrgent = urgent === true
    noticeTimer.restart()
  }

  function membership(args, done) {
    store.run(args, function(doc) {
      if (doc && doc.error && doc.error.message) root.showNotice(doc.error.message, true)
      else if (doc) root.showNotice(done, false)
    })
  }

  function setWatchlist(symbol, category, name, on) {
    membership(on ? ["watchlist", "add", symbol, category, name || symbol] : ["watchlist", "remove", symbol],
               (on ? "Added " : "Removed ") + symbol + (on ? " to" : " from") + " the watchlist")
  }

  function setFavorite(symbol, category, name, on) {
    membership(on ? ["favorite", "add", symbol, category, name || symbol] : ["favorite", "remove", symbol],
               (on ? "Added " : "Removed ") + symbol + (on ? " to" : " from") + " favorites")
  }

  function toggleFavorite(symbol, category, name) {
    setFavorite(symbol, category, name, store.favorites.indexOf(symbol) === -1)
  }

  // IPC `add SYM CAT` and `favorite SYM[:CAT]`.
  function addSymbol(symbol, category) {
    var sym = String(symbol || "").trim().toUpperCase()
    if (sym === "") return
    membership(["watchlist", "add", sym, String(category || "").trim().toLowerCase()], "Added " + sym + " to the watchlist")
  }

  function favoriteSymbol(spec) {
    var s = String(spec || "").trim().toUpperCase()
    if (s === "") return
    var sym = s.split(":")[0]
    var on = store.favorites.indexOf(sym) === -1
    membership(on ? ["favorite", "add", s] : ["favorite", "remove", sym],
               (on ? "Added " : "Removed ") + sym + (on ? " to" : " from") + " favorites")
  }

  // ---- Rows ---------------------------------------------------------------
  readonly property var categoryOrder: ["stock", "crypto", "currency"]
  readonly property var categoryLabels: ({ stock: "Stocks", crypto: "Crypto", currency: "Currencies" })
  readonly property var categoryNames: ({ stock: "Stock", crypto: "Crypto", currency: "Currency" })

  function matchesFilter(inst) {
    var f = filterText.trim().toLowerCase()
    if (f === "") return true
    return String(inst.symbol).toLowerCase().indexOf(f) !== -1
        || String(inst.name || "").toLowerCase().indexOf(f) !== -1
  }

  function instrumentRow(inst, favorites, quotes) {
    var q = quotes[inst.symbol] || null
    var valid = q ? q.valid === true : false
    var detail = ""
    if (!valid) detail = store.busy && !q ? "Pricing…" : "Not priced"
    else if (q.stale) detail = "Last known price"
    return {
      type: "instrument",
      symbol: inst.symbol,
      name: inst.name || inst.symbol,
      category: inst.category,
      label: inst.symbol + " · " + (inst.name || ""),
      favorite: favorites.indexOf(inst.symbol) !== -1,
      starButton: true,
      valid: valid,
      priceText: q && valid ? q.price_text : "—",
      changeText: q && valid ? q.change_text : "",
      dir: q ? q.dir : "flat",
      detail: detail
    }
  }

  function hubRows() {
    var s = root.store
    var counts = { watchlist: 0, favorites: s.favorites.length }
    for (var i = 0; i < s.instruments.length; i++) if (s.instruments[i].in_watchlist !== false) counts.watchlist++
    return [
      { type: "action", icon: "", label: "Search", detail: "Look up a stock, crypto or currency", page: "search" },
      { type: "action", icon: "", label: "Watchlist", detail: counts.watchlist + " tracked", page: "watchlist" },
      { type: "action", icon: "★", label: "Favorites", detail: counts.favorites + " starred, shown in the bar", page: "favorites" },
      { type: "action", icon: "", label: "Portfolio", detail: "Coming in a later version", page: "portfolio", muted: true },
      { type: "action", icon: "", label: "News", detail: "Coming in a later version", page: "news", muted: true },
      { type: "action", icon: "", label: "Data sources", detail: "Who prices what", page: "sources" },
      { type: "action", icon: "", label: "Settings", detail: "Coming in a later version", page: "settings", muted: true }
    ]
  }

  function watchlistRows() {
    var s = root.store
    var out = []
    var first = true
    var shown = 0
    for (var c = 0; c < categoryOrder.length; c++) {
      var cat = categoryOrder[c]
      var group = []
      for (var i = 0; i < s.instruments.length; i++) {
        var inst = s.instruments[i]
        if (inst.category === cat && inst.in_watchlist !== false && matchesFilter(inst)) group.push(inst)
      }
      if (group.length === 0) continue
      if (!first) out.push({ type: "sep" })
      first = false
      out.push({ type: "header", label: categoryLabels[cat] })
      for (var g = 0; g < group.length; g++) out.push(instrumentRow(group[g], s.favorites, s.quotes))
      shown += group.length
    }
    if (shown === 0) {
      if (query !== "") out.push({ type: "note", label: "Nothing on the watchlist matches \"" + query + "\"" })
      else if (s.hasData) out.push({ type: "note", label: "Your watchlist is empty", detail: "Open an instrument and add it from its detail page." })
      else out.push({ type: "note", label: s.busy ? "Fetching prices…" : "No prices yet", detail: "The first snapshot is on its way." })
    }
    return out
  }

  function favoritesRows() {
    var s = root.store
    var out = []
    for (var i = 0; i < s.instruments.length; i++) {
      var inst = s.instruments[i]
      if (inst.is_favorite === true && matchesFilter(inst)) out.push(instrumentRow(inst, s.favorites, s.quotes))
    }
    if (out.length === 0) {
      if (query !== "") out.push({ type: "note", label: "No favorite matches \"" + query + "\"" })
      else out.push({ type: "note", label: "No favorites yet", detail: "Open an instrument and star it from its detail page. Favorites are what the bar strip shows." })
    }
    return out
  }

  function searchRows() {
    var out = []
    var q = query
    if (q === "") {
      out.push({ type: "note", label: "Type a symbol or a name, then press Enter.",
                 detail: "AAPL, HSBA.L, ^GSPC, EURUSD, doge…" })
      return out
    }
    out.push({ type: "action", icon: "", label: "Search markets for \"" + q + "\"",
               detail: searching ? "Searching…" : "Enter to search", action: "search" })
    if (searchResults !== null && searchedQuery.toLowerCase() === q.toLowerCase()) {
      out.push({ type: "sep" })
      if (searchResults.length === 0) {
        out.push({ type: "note", label: "No matches for \"" + q + "\"" })
      } else {
        out.push({ type: "header", label: "Results" })
        for (var i = 0; i < searchResults.length; i++) {
          var r = searchResults[i]
          var fav = store.favorites.indexOf(r.symbol) !== -1
          out.push({
            type: "instrument",
            symbol: r.symbol,
            name: r.name || r.symbol,
            category: r.category,
            label: (fav ? "★ " : "") + r.symbol + " · " + (r.name || ""),
            favorite: fav,
            starButton: false,
            valid: true,
            priceText: categoryNames[r.category] || r.category,
            changeText: "",
            dir: "",
            detail: r.subtitle_text || ""
          })
        }
        for (var a = 0; a < searchAttribution.length; a++)
          out.push({ type: "attribution", label: searchAttribution[a].label || "", url: searchAttribution[a].url || "" })
      }
    }
    return out
  }

  function detailRows() {
    var e = current
    var s = root.store
    var inst = instrumentFor(e.symbol)
    var q = s.quoteFor(e.symbol)
    var valid = q ? q.valid === true : false
    var name = inst ? inst.name : (q && q.name ? q.name : e.name)
    var category = inst ? inst.category : e.category
    var caption
    if (valid) caption = (categoryNames[category] || category) + (q.currency ? " · " + q.currency : "") + (q.stale ? " · last known price" : "")
    else if (s.busy) caption = "Pricing…"
    else caption = "Not priced"
    var out = [{
      type: "hero", symbol: e.symbol, name: name, caption: caption, valid: valid,
      priceText: valid ? q.price_text : "—", changeText: valid ? q.change_text : "", dir: q ? q.dir : "flat"
    }]
    var series = chartSeries && chartSeries.symbol === e.symbol ? chartSeries : null
    var hasChart = series !== null && series.valid === true
    var chartNote = ""
    if (chartError !== "") chartNote = chartError
    else if (!hasChart && chartLoading) chartNote = "Loading " + chartRange + "…"
    else if (!hasChart && series) chartNote = series.message || "No chart for this range"
    else if (hasChart && series.range !== chartRange) chartNote = "Loading " + chartRange + "…"
    else if (hasChart && series.message) chartNote = series.message
    out.push({ type: "chart", series: hasChart ? series : null, loading: chartLoading, note: chartNote,
               changeText: hasChart ? series.range_change_text : "", dir: hasChart ? series.dir : "flat" })
    out.push({ type: "tabs", range: chartRange })
    out.push({ type: "sep" })
    var inWatch = inst ? inst.in_watchlist === true : false
    var isFav = inst ? inst.is_favorite === true : false
    if (inst || valid) {
      out.push({ type: "action", icon: inWatch ? "" : "", label: inWatch ? "Remove from watchlist" : "Add to watchlist",
                 action: "watchlist", symbol: e.symbol, category: category, name: name, on: !inWatch })
      out.push({ type: "action", icon: isFav ? "★" : "☆", label: isFav ? "Remove from favorites" : "Add to favorites",
                 detail: isFav ? "" : "Favorites are what the bar strip shows",
                 action: "favorite", symbol: e.symbol, category: category, name: name, on: !isFav })
    } else if (s.busy) {
      out.push({ type: "note", label: "Pricing " + e.symbol + "…", detail: "Add appears once a provider answers for it." })
    } else {
      out.push({ type: "note", label: e.symbol + " could not be priced", urgent: true,
                 detail: (s.errorText !== "" ? s.errorText + ". " : "") + "Nothing to add until a provider answers for it; r retries." })
    }
    if (notice !== "") {
      out.push({ type: "sep" })
      out.push({ type: "note", label: notice, urgent: noticeUrgent })
    }
    return out
  }

  function soonRows() {
    var out = []
    if (page === "sources") {
      out.push({ type: "note", label: "Stocks, indices and currencies", detail: "Yahoo Finance, no key. Unofficial and delayed; see the README." })
      out.push({ type: "note", label: "Crypto", detail: "CoinGecko, no key." })
      out.push({ type: "sep" })
      out.push({ type: "note", label: "Keys for other providers, demo mode and this page's real form come in a later version." })
    } else if (page === "settings") {
      out.push({ type: "note", label: "The settings page comes in a later version.",
                 detail: "Until then: omarchy bar set, or the costafot.markets entry in ~/.config/omarchy/shell.json." })
    } else {
      out.push({ type: "note", label: pageTitles[page] + " comes in a later version." })
    }
    return out
  }

  readonly property var rows: {
    var s = root.store
    var out = []
    if (!isHub) out.push({ type: "title", label: page === "detail" ? current.symbol : (pageTitles[page] || page) })
    if (s.rateLimitBanner)
      out.push({ type: "note", warn: true, icon: "", label: "Rate-limited — showing last known prices",
                 detail: "Will refresh automatically once the limit clears." })
    var body
    if (page === "hub") body = hubRows()
    else if (page === "watchlist") body = watchlistRows()
    else if (page === "favorites") body = favoritesRows()
    else if (page === "search") body = searchRows()
    else if (page === "detail") body = detailRows()
    else body = soonRows()
    out = out.concat(body)

    var status = s.statusRows
    var errorText = s.errorText
    if (status.length > 0 || errorText !== "") {
      out.push({ type: "sep" })
      for (var r = 0; r < status.length; r++)
        out.push({ type: "note", label: status[r].text || "", detail: status[r].detail || "" })
      if (errorText !== "")
        out.push({ type: "note", label: errorText, urgent: true })
    }
    if (page !== "search") {
      var attribution = s.attribution
      if (attribution.length > 0 || s.generatedAt > 0) {
        out.push({ type: "sep" })
        for (var a = 0; a < attribution.length; a++)
          out.push({ type: "attribution", label: attribution[a].label || "", url: attribution[a].url || "" })
        if (s.generatedAt > 0) {
          var stamp = Qt.formatTime(new Date(s.generatedAt * 1000), "HH:mm")
          out.push({ type: "footer",
                     label: (s.stale ? "Last good update " : "Updated ") + stamp
                            + (s.rateLimited ? " · rate-limited" : "")
                            + (s.demo ? " · demo data" : "") })
        }
      }
    }
    out.push({ type: "footer", label: keyHint() })
    return out
  }

  function keyHint() {
    if (page === "hub") return "j/k move · Enter opens · r refreshes · Esc closes"
    if (page === "search") return "Enter searches, then opens · Tab to the list · Esc back"
    if (hasField) return "Type to filter · ↑/↓ move · Enter opens · Esc back"
    if (page === "detail") return "←/→ or 1–5 range · Enter applies · r refreshes · Esc back"
    return "Esc or Backspace back"
  }

  // ---- Cursor -------------------------------------------------------------
  property int selectedIndex: -1

  // Hover moves the cursor only when the pointer itself moved: rows that
  // re-layout under a resting pointer (search results landing) get a
  // synthetic move and must not steal the cursor from the keyboard.
  property point lastPointer: Qt.point(-1, -1)

  function hoverRow(index, item, x, y) {
    var p = item.mapToItem(null, x, y)
    if (p.x === lastPointer.x && p.y === lastPointer.y) return
    lastPointer = p
    selectedIndex = index
  }

  function isCursorRow(row) {
    return row && (row.type === "instrument" || row.type === "attribution" || row.type === "action")
  }

  function firstCursorIndex() {
    for (var i = 0; i < rows.length; i++) if (isCursorRow(rows[i])) return i
    return -1
  }

  function firstIndexOfType(type) {
    for (var i = 0; i < rows.length; i++) if (rows[i].type === type) return i
    return -1
  }

  onRowsChanged: {
    if (selectedIndex < 0 || selectedIndex >= rows.length || !isCursorRow(rows[selectedIndex]))
      selectedIndex = firstCursorIndex()
  }

  function moveCursor(dy) {
    var cursorRows = []
    for (var i = 0; i < rows.length; i++) if (isCursorRow(rows[i])) cursorRows.push(i)
    if (cursorRows.length === 0) return
    var pos = cursorRows.indexOf(selectedIndex)
    if (pos === -1) pos = dy > 0 ? -1 : 0
    pos = (pos + dy + cursorRows.length) % cursorRows.length
    selectedIndex = cursorRows[pos]
    ensureCursorVisible()
  }

  // Scroll the list so the cursor row is on screen (j/k past the fold).
  function ensureCursorVisible() {
    var item = rowRepeater.itemAt(selectedIndex)
    if (!item) return
    var top = item.y
    var bottom = item.y + item.height
    if (top < listScroll.contentY) listScroll.contentY = top
    else if (bottom > listScroll.contentY + listScroll.height)
      listScroll.contentY = Math.max(0, bottom - listScroll.height)
  }

  function activate(row) {
    if (!row) return
    if (row.type === "attribution" && row.url) {
      root.close()
      Qt.openUrlExternally(row.url)
    } else if (row.type === "instrument") {
      openDetail(row.symbol, row.name, row.category)
    } else if (row.type === "action") {
      if (row.page) push({ page: row.page })
      else if (row.action === "search") runSearch()
      else if (row.action === "watchlist") setWatchlist(row.symbol, row.category, row.name, row.on)
      else if (row.action === "favorite") setFavorite(row.symbol, row.category, row.name, row.on)
    } else if (row.type === "title") {
      pop()
    }
  }

  onOpenedChanged: {
    if (opened) {
      store.refresh(false)
      notice = ""
      if (pendingStack) {
        stack = pendingStack
        pendingStack = null
        enterPage(stack[stack.length - 1])
      } else {
        goHome()
      }
    }
  }

  // A poll landed while a chart is up: reload the visible range (a cache
  // read inside the helper's five minutes, a fetch after).
  property Connections storeWatch: Connections {
    target: root.store
    function onGeneratedAtChanged() { if (root.opened && root.page === "detail") root.loadChart(root.chartRange, false) }
  }

  property Timer noticeTimer: Timer {
    interval: 3000
    repeat: false
    onTriggered: root.notice = ""
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: root.hasField ? filterField : keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(
      contentColumn.implicitHeight + (root.hasField ? filterField.height + Style.space(6) : 0), Style.space(760))

    // Unhandled keys from the catcher (it never accepts Backspace) land here.
    Item {
      id: pageArea
      anchors.fill: parent

      Keys.onPressed: function(event) {
        if (event.key === Qt.Key_Backspace && !filterField.activeFocus) {
          root.pop()
          event.accepted = true
        }
      }

      PanelKeyCatcher {
        id: keyCatcher
        anchors.fill: parent
        clip: true
        blocked: filterField.activeFocus
        onMoveRequested: function(dx, dy) {
          if (dy !== 0) root.moveCursor(dy)
          else if (dx !== 0 && root.page === "detail") root.stepRange(dx)
        }
        onActivateRequested: root.activate(root.rows[root.selectedIndex])
        onCloseRequested: root.pop()
        onTabRequested: function(direction) { root.switchPanel(direction) }
        onTextKey: function(t) {
          if (t === "r" || t === "R") root.refresh()
          else if (t === "/" && root.hasField) filterField.forceActiveFocus()
          else if (root.page === "detail" && t >= "1" && t <= "5") root.setRange(root.chartRanges[Number(t) - 1])
        }

        Item {
                    anchors.fill: parent

          // The filter / query box the list pages start in. Up/Down, Enter,
          // Escape and Tab are forwarded; everything else edits the text.
          TextField {
            id: filterField
            visible: root.hasField
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: Style.space(8)
            anchors.rightMargin: Style.space(8)
            foreground: root.contentForeground
            font.family: root.contentFontFamily
            placeholderText: root.page === "search" ? "Symbol or name, then Enter"
                           : root.page === "watchlist" ? "Filter the watchlist" : "Filter favorites"

            Keys.onPressed: function(event) {
              if (event.key === Qt.Key_Escape) {
                root.pop()
                event.accepted = true
              } else if (event.key === Qt.Key_Down) {
                root.moveCursor(1)
                event.accepted = true
              } else if (event.key === Qt.Key_Up) {
                root.moveCursor(-1)
                event.accepted = true
              } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                root.activate(root.rows[root.selectedIndex])
                event.accepted = true
              } else if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) {
                keyCatcher.forceActiveFocus()
                event.accepted = true
              } else if (event.key === Qt.Key_Backspace && text === "") {
                root.pop()
                event.accepted = true
              }
            }
          }

          Flickable {
            id: listScroll
            anchors.top: root.hasField ? filterField.bottom : parent.top
            anchors.topMargin: root.hasField ? Style.space(6) : 0
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            contentWidth: width
            contentHeight: contentColumn.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            interactive: contentHeight > height

            Column {
              id: contentColumn
              width: listScroll.width
              spacing: Style.space(2)

              Repeater {
                id: rowRepeater
                model: root.rows

                delegate: Item {
                  id: rowItem
                  required property var modelData
                  required property int index

                  readonly property string kind: modelData.type
                  readonly property bool isInstrument: kind === "instrument"
                  readonly property bool isAttribution: kind === "attribution"
                  readonly property bool isAction: kind === "action"
                  readonly property bool isTitle: kind === "title"
                  readonly property bool isChart: kind === "chart"
                  readonly property bool isTabs: kind === "tabs"
                  readonly property bool cursorable: isInstrument || isAttribution || isAction
                  readonly property bool hasCursor: cursorable && index === root.selectedIndex
                  readonly property bool twoLine: (isInstrument || isAction) && !!modelData.detail
                  readonly property color rowForeground: (isInstrument && !modelData.valid) || (isAction && modelData.muted === true)
                    ? root.mutedForeground : root.contentForeground

                  width: contentColumn.width
                  height: kind === "sep" ? Style.space(11)
                    : kind === "header" ? headerLabel.implicitHeight + Style.space(8)
                    : kind === "title" ? Style.space(30)
                    : kind === "hero" ? heroColumn.implicitHeight + Style.space(16)
                    : kind === "note" ? noteColumn.implicitHeight + Style.space(12)
                    : kind === "chart" ? chartLoader.implicitHeight + Style.space(12)
                    : kind === "tabs" ? Style.space(30)
                    : kind === "footer" ? footerLabel.implicitHeight + Style.space(8)
                    : twoLine ? Style.space(44) : Style.space(32)

                  PanelSeparator {
                    visible: rowItem.kind === "sep"
                    anchors.verticalCenter: parent.verticalCenter
                    foreground: root.contentForeground
                  }

                  PanelSectionHeader {
                    id: headerLabel
                    visible: rowItem.kind === "header"
                    text: rowItem.kind === "header" ? rowItem.modelData.label : ""
                    foreground: root.contentForeground
                    fontFamily: root.contentFontFamily
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: Style.space(2)
                  }

                  // ‹ Page title — clicking it goes back.
                  Item {
                    visible: rowItem.isTitle
                    anchors.fill: parent

                    Row {
                      anchors.fill: parent
                      anchors.leftMargin: Style.space(8)
                      spacing: Style.space(8)

                      Text {
                        height: parent.height
                        textFormat: Text.PlainText
                        text: "‹"
                        color: root.mutedForeground
                        font.family: root.contentFontFamily
                        font.pixelSize: Style.font.title
                        verticalAlignment: Text.AlignVCenter
                      }

                      Text {
                        height: parent.height
                        textFormat: Text.PlainText
                        text: rowItem.isTitle ? rowItem.modelData.label : ""
                        color: root.contentForeground
                        font.family: root.contentFontFamily
                        font.pixelSize: Style.font.title
                        font.bold: true
                        verticalAlignment: Text.AlignVCenter
                      }
                    }

                    MouseArea {
                      anchors.fill: parent
                      cursorShape: Qt.PointingHandCursor
                      onClicked: root.pop()
                    }
                  }

                  // Detail hero: symbol, name, price, change.
                  Column {
                    id: heroColumn
                    visible: rowItem.kind === "hero"
                    width: parent.width - Style.space(16)
                    x: Style.space(8)
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Style.space(2)

                    Text {
                      width: parent.width
                      textFormat: Text.PlainText
                      text: rowItem.kind === "hero" ? rowItem.modelData.symbol : ""
                      color: root.contentForeground
                      font.family: root.contentFontFamily
                      font.pixelSize: Style.font.heading
                      font.bold: true
                      elide: Text.ElideRight
                    }

                    Text {
                      width: parent.width
                      textFormat: Text.PlainText
                      text: rowItem.kind === "hero" ? rowItem.modelData.name : ""
                      color: root.mutedForeground
                      font.family: root.contentFontFamily
                      font.pixelSize: Style.font.body
                      elide: Text.ElideRight
                    }

                    Item { width: 1; height: Style.space(6) }

                    Row {
                      width: parent.width
                      spacing: Style.space(10)

                      Text {
                        textFormat: Text.PlainText
                        text: rowItem.kind === "hero" ? rowItem.modelData.priceText : ""
                        color: rowItem.kind === "hero" && rowItem.modelData.valid ? root.contentForeground : root.mutedForeground
                        font.family: root.contentFontFamily
                        font.pixelSize: Style.font.display
                        font.bold: true
                      }

                      Text {
                        visible: text !== ""
                        anchors.bottom: parent.bottom
                        anchors.bottomMargin: Style.space(4)
                        textFormat: Text.PlainText
                        text: rowItem.kind === "hero" ? rowItem.modelData.changeText : ""
                        color: root.store.dirColor(rowItem.modelData.dir, root.contentForeground)
                        font.family: root.contentFontFamily
                        font.pixelSize: Style.font.title
                      }
                    }

                    Text {
                      width: parent.width
                      textFormat: Text.PlainText
                      text: rowItem.kind === "hero" ? rowItem.modelData.caption : ""
                      color: root.mutedForeground
                      font.family: root.contentFontFamily
                      font.pixelSize: Style.font.caption
                      elide: Text.ElideRight
                    }
                  }

                  // The chart: one Canvas, loaded only for this row kind, plus
                  // the range's move under it (or why there is no chart yet).
                  Loader {
                    id: chartLoader
                    active: rowItem.isChart
                    visible: rowItem.isChart
                    width: parent.width - Style.space(16)
                    x: Style.space(8)
                    anchors.verticalCenter: parent.verticalCenter

                    sourceComponent: Column {
                      width: chartLoader.width
                      spacing: Style.space(4)

                      Chart {
                        width: parent.width
                        points: rowItem.modelData.series ? rowItem.modelData.series.points : []
                        previousClose: rowItem.modelData.series && rowItem.modelData.series.previous_close !== null
                          && rowItem.modelData.series.previous_close !== undefined
                          ? Number(rowItem.modelData.series.previous_close) : NaN
                        up: rowItem.modelData.dir !== "down"
                        upColor: root.store.upColor
                        downColor: root.store.downColor
                        foreground: root.contentForeground
                        mutedForeground: root.mutedForeground
                        fontFamily: root.contentFontFamily
                        minText: rowItem.modelData.series ? rowItem.modelData.series.min_text : ""
                        maxText: rowItem.modelData.series ? rowItem.modelData.series.max_text : ""
                        previousCloseText: rowItem.modelData.series ? rowItem.modelData.series.previous_close_text : ""
                        firstLabel: rowItem.modelData.series ? rowItem.modelData.series.first_label : ""
                        lastLabel: rowItem.modelData.series ? rowItem.modelData.series.last_label : ""
                        loading: rowItem.modelData.loading === true && rowItem.modelData.series === null
                      }

                      Text {
                        visible: text !== ""
                        width: parent.width
                        textFormat: Text.PlainText
                        text: rowItem.modelData.changeText || ""
                        color: root.store.dirColor(rowItem.modelData.dir, root.contentForeground)
                        font.family: root.contentFontFamily
                        font.pixelSize: Style.font.body
                        elide: Text.ElideRight
                      }

                      Text {
                        visible: text !== ""
                        width: parent.width
                        textFormat: Text.PlainText
                        text: rowItem.modelData.note || ""
                        color: root.mutedForeground
                        font.family: root.contentFontFamily
                        font.pixelSize: Style.font.caption
                        wrapMode: Text.Wrap
                      }
                    }
                  }

                  // 1D 1W 1M 1Y 5Y — the selected range filled, the rest plain.
                  Row {
                    visible: rowItem.isTabs
                    x: Style.space(8)
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Style.space(4)

                    Repeater {
                      model: rowItem.isTabs ? root.chartRanges : []

                      Rectangle {
                        id: tab
                        required property string modelData
                        readonly property bool selected: rowItem.isTabs && rowItem.modelData.range === modelData
                        width: Style.space(40)
                        height: Style.space(22)
                        radius: Style.cornerRadius
                        color: selected ? Style.selectedFillFor(root.contentForeground, Color.accent, root.urgentForeground)
                             : tabHover.containsMouse ? Style.hoverFillFor(root.contentForeground, Color.accent, root.urgentForeground)
                             : "transparent"

                        Text {
                          anchors.centerIn: parent
                          textFormat: Text.PlainText
                          text: tab.modelData
                          color: tab.selected ? root.contentForeground : root.mutedForeground
                          font.family: root.contentFontFamily
                          font.pixelSize: Style.font.caption
                          font.bold: tab.selected
                        }

                        MouseArea {
                          id: tabHover
                          anchors.fill: parent
                          hoverEnabled: true
                          cursorShape: Qt.PointingHandCursor
                          onClicked: root.setRange(tab.modelData)
                        }
                      }
                    }
                  }

                  Column {
                    id: noteColumn
                    visible: rowItem.kind === "note"
                    width: parent.width - Style.space(16)
                    x: Style.space(8)
                    spacing: Style.space(3)
                    anchors.verticalCenter: parent.verticalCenter

                    Text {
                      width: parent.width
                      textFormat: Text.PlainText
                      text: (rowItem.modelData.icon ? rowItem.modelData.icon + "  " : "") + (rowItem.modelData.label || "")
                      color: rowItem.modelData.urgent ? root.urgentForeground
                           : rowItem.modelData.warn ? root.store.warnColor : root.contentForeground
                      font.family: root.contentFontFamily
                      font.pixelSize: rowItem.modelData.warn ? Style.font.body : Style.font.caption
                      wrapMode: Text.Wrap
                    }

                    Text {
                      visible: !!rowItem.modelData.detail
                      width: parent.width
                      textFormat: Text.PlainText
                      text: rowItem.modelData.detail || ""
                      color: root.mutedForeground
                      font.family: root.contentFontFamily
                      font.pixelSize: Style.font.caption
                      wrapMode: Text.Wrap
                    }
                  }

                  Text {
                    id: footerLabel
                    visible: rowItem.kind === "footer"
                    width: parent.width - Style.space(16)
                    x: Style.space(8)
                    anchors.verticalCenter: parent.verticalCenter
                    textFormat: Text.PlainText
                    text: rowItem.kind === "footer" ? rowItem.modelData.label : ""
                    color: root.mutedForeground
                    font.family: root.contentFontFamily
                    font.pixelSize: Style.font.caption
                    elide: Text.ElideRight
                  }

                  CursorSurface {
                    visible: rowItem.cursorable
                    anchors.fill: parent
                    hasCursor: rowItem.hasCursor
                    foreground: root.contentForeground
                    accent: Color.accent

                    MouseArea {
                      anchors.fill: parent
                      hoverEnabled: true
                      cursorShape: rowItem.isAttribution ? Qt.PointingHandCursor : Qt.ArrowCursor
                      onPositionChanged: function(mouse) { root.hoverRow(rowItem.index, rowItem, mouse.x, mouse.y) }
                      onClicked: root.activate(rowItem.modelData)
                    }

                    // ★ SYM · Name                         $64,210.00  ▲ +1.20%
                    Row {
                      visible: rowItem.isInstrument
                      anchors.fill: parent
                      anchors.leftMargin: Style.space(4)
                      anchors.rightMargin: Style.space(8)
                      spacing: Style.space(6)

                      Item {
                        width: Style.space(22)
                        height: parent.height

                        // Rows that manage membership get a real star button;
                        // search results only navigate (as on Windows).
                        PanelActionButton {
                          visible: rowItem.isInstrument && rowItem.modelData.starButton === true
                          anchors.centerIn: parent
                          z: 1
                          iconText: rowItem.modelData.favorite ? "★" : "☆"
                          tooltipText: rowItem.modelData.favorite ? "Remove from favorites" : "Add to favorites"
                          foreground: rowItem.modelData.favorite ? root.contentForeground : root.mutedForeground
                          fontFamily: root.contentFontFamily
                          fontSize: Style.font.body
                          onClicked: root.toggleFavorite(rowItem.modelData.symbol, rowItem.modelData.category, rowItem.modelData.name)
                        }
                      }

                      Column {
                        width: parent.width - Style.space(22) - valueColumn.width - parent.spacing * 2
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: Style.space(1)

                        Text {
                          width: parent.width
                          textFormat: Text.PlainText
                          text: rowItem.modelData.label || ""
                          color: rowItem.rowForeground
                          font.family: root.contentFontFamily
                          font.pixelSize: Style.font.body
                          elide: Text.ElideRight
                        }

                        Text {
                          visible: rowItem.twoLine
                          width: parent.width
                          textFormat: Text.PlainText
                          text: rowItem.modelData.detail || ""
                          color: root.mutedForeground
                          font.family: root.contentFontFamily
                          font.pixelSize: Style.font.caption
                          elide: Text.ElideRight
                        }
                      }

                      Row {
                        id: valueColumn
                        height: parent.height
                        spacing: Style.space(8)

                        Text {
                          height: parent.height
                          textFormat: Text.PlainText
                          text: rowItem.modelData.priceText || ""
                          color: rowItem.rowForeground
                          font.family: root.contentFontFamily
                          font.pixelSize: Style.font.body
                          verticalAlignment: Text.AlignVCenter
                        }

                        Text {
                          visible: text !== ""
                          height: parent.height
                          textFormat: Text.PlainText
                          text: rowItem.modelData.changeText || ""
                          color: root.store.dirColor(rowItem.modelData.dir, rowItem.rowForeground)
                          font.family: root.contentFontFamily
                          font.pixelSize: Style.font.body
                          verticalAlignment: Text.AlignVCenter

                          Behavior on color {
                            enabled: !root.bar || root.bar.foregroundAnimationEnabled
                            ColorAnimation { duration: 160 }
                          }
                        }
                      }
                    }

                    //  Label                                      detail below
                    Row {
                      visible: rowItem.isAction
                      anchors.fill: parent
                      anchors.leftMargin: Style.space(8)
                      anchors.rightMargin: Style.space(8)
                      spacing: Style.space(10)

                      Text {
                        width: Style.space(18)
                        height: parent.height
                        textFormat: Text.PlainText
                        text: rowItem.isAction ? (rowItem.modelData.icon || "") : ""
                        color: rowItem.rowForeground
                        font.family: root.contentFontFamily
                        font.pixelSize: Style.font.body
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                      }

                      Column {
                        width: parent.width - Style.space(18) - parent.spacing
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: Style.space(1)

                        Text {
                          width: parent.width
                          textFormat: Text.PlainText
                          text: rowItem.isAction ? (rowItem.modelData.label || "") : ""
                          color: rowItem.rowForeground
                          font.family: root.contentFontFamily
                          font.pixelSize: Style.font.body
                          elide: Text.ElideRight
                        }

                        Text {
                          visible: rowItem.twoLine
                          width: parent.width
                          textFormat: Text.PlainText
                          text: rowItem.modelData.detail || ""
                          color: root.mutedForeground
                          font.family: root.contentFontFamily
                          font.pixelSize: Style.font.caption
                          elide: Text.ElideRight
                        }
                      }
                    }

                    Text {
                      visible: rowItem.isAttribution
                      anchors.fill: parent
                      anchors.leftMargin: Style.space(8)
                      anchors.rightMargin: Style.space(8)
                      textFormat: Text.PlainText
                      text: rowItem.modelData.label || ""
                      color: root.mutedForeground
                      font.family: root.contentFontFamily
                      font.pixelSize: Style.font.caption
                      verticalAlignment: Text.AlignVCenter
                      elide: Text.ElideRight
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
