pragma ComponentBehavior: Bound
import QtQuick
import Quickshell.Io
import qs.Commons

// The data side of the widget: runs bin/markets, keeps the last snapshot,
// polls on a timer and owns the two direction colours. Nothing here draws.
// The helper does every network call, every disk write and every number
// format; this object parses one JSON line and holds it.
QtObject {
  id: store

  // Injected by Panel.qml (which the bar injects in turn).
  property var settings: ({})
  property string pluginDir: ""

  // ---- Settings -----------------------------------------------------------
  function setting(key, fallback) {
    var v = settings ? settings[key] : undefined
    return v === undefined || v === null ? fallback : v
  }

  // The non-secret scalars the helper understands. Keys it does not know are
  // ignored on its side, so this list can lead the helper by a version.
  readonly property var helperSettingKeys: ["strip", "stripShowPrice", "stripMax", "portfolioCurrency", "showRateLimitErrors"]

  // Serialised once so a re-injection of identical settings (every remount
  // does one) is a no-op instead of a refetch.
  readonly property string settingsJson: {
    var out = {}
    for (var i = 0; i < helperSettingKeys.length; i++) {
      var k = helperSettingKeys[i]
      var v = settings ? settings[k] : undefined
      if (v !== undefined && v !== null) out[k] = v
    }
    return JSON.stringify(out)
  }
  onSettingsJsonChanged: if (snapshot) refresh(false)

  readonly property int refreshMinutes: Math.max(0, Math.round(Number(setting("refreshMinutes", 10)) || 0))
  readonly property bool showRateLimitErrors: setting("showRateLimitErrors", true) !== false

  // ---- Snapshot -----------------------------------------------------------
  // The last document with data in it. Never cleared by a failure: a bad run
  // sets `lastError` and `stale`, the prices stay.
  property var snapshot: null
  property string lastError: ""
  property string lastErrorCode: ""
  // True when the newest run failed, so what is shown is older than it looks.
  property bool stale: false

  readonly property bool hasData: snapshot !== null
  readonly property var strip: snapshot && Array.isArray(snapshot.strip) ? snapshot.strip : []
  readonly property var instruments: snapshot && Array.isArray(snapshot.instruments) ? snapshot.instruments : []
  readonly property var quotes: snapshot && snapshot.quotes ? snapshot.quotes : ({})
  readonly property var favorites: snapshot && Array.isArray(snapshot.favorites) ? snapshot.favorites : []
  // The priced holdings and their totals, every string ready to paint; the
  // helper prices held symbols with the watchlist and converts them into
  // the reporting currency, so this is never computed here.
  readonly property var portfolio: snapshot && snapshot.portfolio && snapshot.portfolio.totals ? snapshot.portfolio : null
  readonly property var held: snapshot && Array.isArray(snapshot.held) ? snapshot.held : []
  readonly property var attribution: snapshot && Array.isArray(snapshot.attribution) ? snapshot.attribution : []
  // From the newest document of any kind: the helper's latch outlives a
  // process (rate-limit.json), so a cached snapshot after a throttled poll
  // still says so, and a throttled chart or search call raises it too.
  property bool rateLimited: false
  // The amber banner (RateLimitHint): only when the user wants it.
  readonly property bool rateLimitBanner: rateLimited && showRateLimitErrors
  readonly property int generatedAt: snapshot && snapshot.generated_at ? Number(snapshot.generated_at) : 0

  // Status rows the helper wants shown, minus the rate-limit row: the
  // panel paints that one as the banner at the top instead.
  readonly property var statusRows: {
    var rows = snapshot && Array.isArray(snapshot.status_rows) ? snapshot.status_rows : []
    var out = []
    for (var i = 0; i < rows.length; i++) if (rows[i].kind !== "rate_limited") out.push(rows[i])
    return out
  }

  // The one error line the panel prints, if any.
  readonly property string errorText: {
    if (lastError === "") return ""
    if (!showRateLimitErrors && lastErrorCode === "rate_limited") return ""
    return lastError
  }

  function quoteFor(symbol) {
    var q = quotes[symbol]
    return q ? q : null
  }

  function positionFor(symbol) {
    var rows = portfolio && Array.isArray(portfolio.positions) ? portfolio.positions : []
    for (var i = 0; i < rows.length; i++) if (rows[i].symbol === symbol) return rows[i]
    return null
  }

  // ---- Direction colours --------------------------------------------------
  // The theme's `green`/`red` (the shell reads `red || color1` for urgent, so
  // the same ANSI slots are the second choice), then accent/urgent.
  property string themeGreen: ""
  property string themeRed: ""
  property string themeYellow: ""
  readonly property color upColor: themeGreen !== "" ? themeGreen : Color.accent
  readonly property color downColor: themeRed !== "" ? themeRed : Color.urgent
  // Caution, not failure: the theme's yellow, else the Windows banner's amber.
  readonly property color warnColor: themeYellow !== "" ? themeYellow : "#c87c00"

  function parseThemeColors(raw) {
    var lines = String(raw || "").split("\n")
    var found = {}
    for (var i = 0; i < lines.length; i++) {
      var m = lines[i].match(/^\s*([A-Za-z0-9_-]+)\s*=\s*["']?(#[0-9A-Fa-f]{6})/)
      if (m) found[m[1]] = m[2]
    }
    themeGreen = found.green || found.color2 || ""
    themeRed = found.red || found.color1 || ""
    themeYellow = found.yellow || found.color3 || ""
  }

  // `flat` renders like `up` (the Windows UiQuote.IsUp rule).
  function dirColor(dir, base) {
    if (dir === "down") return downColor
    if (dir === "up" || dir === "flat") return upColor
    return base
  }

  property FileView colorsFile: FileView {
    path: Color.currentThemePath + "/colors.toml"
    watchChanges: true
    printErrors: false
    onLoaded: store.parseThemeColors(text())
    onLoadFailed: store.parseThemeColors("")
    onFileChanged: reload()
  }

  // ---- Running the helper -------------------------------------------------
  // One process at a time; a request made while one runs replaces any
  // earlier waiting request (last command wins). Both the exit code and the
  // collected stdout have to arrive before a run is finalised, in either
  // order, hence the two flags.
  property bool collectorDone: true
  property bool processDone: true
  readonly property bool busy: !collectorDone || !processDone
  property string capturedText: ""
  property int exitCode: 0
  property bool sawExit: false
  property bool tripwireFired: false
  property var pendingRun: null
  property var currentRun: null

  // Untracked symbols a detail page is showing, as "SYM:category". Every
  // snapshot prices them too, so the hero does not blank on the next poll;
  // with --max-age only the symbols older than the window are fetched, so
  // an extra costs one call for itself, not a refetch of the watchlist.
  property var extras: []

  function refresh(force) {
    var args = ["snapshot", "--max-age", force ? "0" : "30"]
    if (extras.length > 0) args = args.concat(["--extra"], extras)
    run(args, null)
  }

  // Runs one helper command. `onDone(doc)` gets the parsed document (or null
  // when the output was unusable); sections the document carries that the
  // snapshot also has (`quotes instruments favorites portfolio held strip`)
  // are merged into the snapshot first, so a mutation re-renders with no
  // second call.
  function run(args, onDone) {
    var job = { args: args, onDone: onDone }
    if (busy) { pendingRun = job; return }
    currentRun = job
    collectorDone = false
    processDone = false
    capturedText = ""
    sawExit = false
    tripwireFired = false
    exitCode = 0
    // Through sh, never direct: handing Quickshell a binary that cannot
    // start can take the whole shell down before a QML signal fires. sh
    // always starts; a failed exec is sh exiting 126/127.
    proc.command = ["/bin/sh", "-c", 'exec "$0" "$@"', "python3",
                    pluginDir + "/bin/markets", "--settings", settingsJson].concat(args)
    proc.running = true
  }

  function maybeFinalize() {
    if (!collectorDone || !processDone) return
    exitFallback.stop()
    finalizeRun()
  }

  function fail(code, message) {
    lastErrorCode = code
    lastError = message
    stale = true
  }

  function finalizeRun() {
    var job = currentRun
    currentRun = null
    var text = capturedText.trim()
    var doc = null
    if (text === "") {
      if (tripwireFired) {
        // Already explained.
      } else if (!sawExit || exitCode === 126 || exitCode === 127) {
        fail("internal", "python3 could not start (exit " + exitCode + ")")
      } else {
        fail("internal", "The markets helper produced no output (exit " + exitCode + ")")
      }
    } else {
      doc = handle(text)
    }
    if (job && typeof job.onDone === "function") job.onDone(doc)
    if (pendingRun) {
      var next = pendingRun
      pendingRun = null
      Qt.callLater(function() { store.run(next.args, next.onDone) })
    }
  }

  function handle(text) {
    var d
    try {
      d = JSON.parse(text)
    } catch (e) {
      fail("internal", "The markets helper returned unparseable output" + (exitCode !== 0 ? " (exit " + exitCode + ")" : ""))
      return null
    }
    if (!d || d.schema_version !== 1) {
      fail("internal", "The markets helper returned an unexpected document (not schema_version 1)")
      return null
    }
    // "Has data" and "has error" are independent: an outage document still
    // carries last-good prices, and those replace the snapshot.
    var hasData = d.strip !== undefined || d.quotes !== undefined
    if (hasData) {
      if (d.command === "snapshot") {
        snapshot = d
      } else {
        var merged = snapshot ? Object.assign({}, snapshot) : { schema_version: 1, command: "snapshot" }
        var sections = ["instruments", "favorites", "portfolio", "held", "strip"]
        for (var i = 0; i < sections.length; i++)
          if (d[sections[i]] !== undefined) merged[sections[i]] = d[sections[i]]
        // Quotes merge additively: a membership document carries the tracked
        // symbols only, and a detail page may be showing an untracked one.
        if (d.quotes !== undefined) merged.quotes = Object.assign({}, merged.quotes || {}, d.quotes)
        // A mutation that fetched nothing credits nobody; keep the snapshot's.
        if (Array.isArray(d.attribution) && d.attribution.length > 0) merged.attribution = d.attribution
        if (d.status_rows !== undefined) merged.status_rows = d.status_rows
        snapshot = merged
      }
    }
    rateLimited = d.rate_limited === true
    if (d.error && d.error.message) {
      fail(String(d.error.code || "internal"), String(d.error.message))
    } else {
      lastError = ""
      lastErrorCode = ""
      stale = false
    }
    return d
  }

  property Process proc: Process {
    // A command that cannot start emits neither `started` nor `exited`;
    // `running` dropping back to false is the only signal.
    onRunningChanged: {
      if (running) return
      store.processDone = true
      exitFallback.restart()
      store.maybeFinalize()
    }
    onExited: function(code) {
      store.sawExit = true
      store.exitCode = code
      store.processDone = true
      exitFallback.restart()
      store.maybeFinalize()
    }
    stdout: StdioCollector {
      waitForEnd: true
      // A tripwire in UTF-16 units, not a byte cap (the helper enforces the
      // real 1 MiB cap on what it reads). It refuses to retain an answer
      // that could not have come from a healthy run.
      readonly property int maxChars: 1024 * 1024
      onStreamFinished: {
        if (text.length > maxChars) {
          store.tripwireFired = true
          store.capturedText = ""
          store.fail("too_large", "The markets helper returned more than " + (maxChars / 1024) + "K characters; refusing it")
        } else {
          store.capturedText = text
        }
        store.collectorDone = true
        store.maybeFinalize()
      }
    }
  }

  property Timer exitFallback: Timer {
    id: exitFallback
    interval: 300
    repeat: false
    onTriggered: {
      store.collectorDone = true
      store.maybeFinalize()
    }
  }

  // The poll. Every bar instance has one; `--max-age 30` makes all but the
  // first on each cycle a cache read.
  property Timer pollTimer: Timer {
    interval: Math.max(1, store.refreshMinutes) * 60000
    running: store.refreshMinutes > 0
    repeat: true
    onTriggered: store.refresh(false)
  }

  // Deferred: settings are injected after the panel's Loader resolves, and a
  // change to them triggers its own (cache-hot) refresh.
  Component.onCompleted: Qt.callLater(function() { store.refresh(false) })
}
