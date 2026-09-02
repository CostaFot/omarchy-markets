pragma ComponentBehavior: Bound
import QtQuick
import qs.Commons
import qs.Ui

// Popup for the Markets bar widget. This version has one page: the watchlist
// grouped by class, with the helper's status lines and attribution under it.
// The panel owns the Store, so the strip in the bar and the rows in here are
// the same document.
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

  readonly property string pluginDir: {
    var dir = Qt.resolvedUrl(".").toString()
    return dir.replace(/^file:\/\//, "").replace(/\/$/, "")
  }

  readonly property Store store: Store {
    pluginDir: root.pluginDir
    settings: root.settings
  }

  function refresh() { store.refresh(true) }

  // ---- Rows ---------------------------------------------------------------
  readonly property var categoryOrder: ["stock", "crypto", "currency"]
  readonly property var categoryLabels: ({ stock: "Stocks", crypto: "Crypto", currency: "Currencies" })

  readonly property var rows: {
    var out = []
    var s = root.store
    var quotes = s.quotes
    var favorites = s.favorites
    var instruments = s.instruments
    var first = true
    for (var c = 0; c < categoryOrder.length; c++) {
      var cat = categoryOrder[c]
      var group = []
      for (var i = 0; i < instruments.length; i++) {
        var inst = instruments[i]
        if (inst.category === cat && inst.in_watchlist !== false) group.push(inst)
      }
      if (group.length === 0) continue
      if (!first) out.push({ type: "sep" })
      first = false
      out.push({ type: "header", label: categoryLabels[cat] })
      for (var g = 0; g < group.length; g++) {
        var q = quotes[group[g].symbol] || null
        var valid = q ? q.valid === true : false
        var detail = ""
        if (!valid) detail = "No provider yet"
        else if (q.stale) detail = "Last known price"
        out.push({
          type: "instrument",
          symbol: group[g].symbol,
          label: group[g].symbol + " · " + (group[g].name || ""),
          favorite: favorites.indexOf(group[g].symbol) !== -1,
          valid: valid,
          priceText: q && valid ? q.price_text : "—",
          changeText: q && valid ? q.change_text : "",
          dir: q ? q.dir : "flat",
          detail: detail
        })
      }
    }
    if (instruments.length === 0) {
      out.push({ type: "note",
                 label: s.hasData ? "The watchlist is empty" : (s.busy ? "Fetching prices…" : "No prices yet"),
                 detail: s.hasData ? "" : "The first snapshot is on its way." })
    }
    var status = s.statusRows
    var errorText = s.errorText
    if (status.length > 0 || errorText !== "") {
      out.push({ type: "sep" })
      for (var r = 0; r < status.length; r++)
        out.push({ type: "note", label: status[r].text || "" })
      if (errorText !== "")
        out.push({ type: "note", label: errorText, urgent: true })
    }
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
                          + (s.demo ? " · demo data" : "")
                          + " · r refreshes" })
      }
    }
    return out
  }

  property int selectedIndex: -1

  function isCursorRow(row) { return row && (row.type === "instrument" || row.type === "attribution") }

  function firstCursorIndex() {
    for (var i = 0; i < rows.length; i++) if (isCursorRow(rows[i])) return i
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
    }
    // Instrument rows open the detail page in a later version.
  }

  onOpenedChanged: {
    if (opened) {
      store.refresh(false)
      selectedIndex = firstCursorIndex()
      listScroll.contentY = 0
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(360))
    contentHeight: panel.fittedContentHeight(contentColumn.implicitHeight, Style.space(760))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      clip: true
      onMoveRequested: function(dx, dy) { if (dy !== 0) root.moveCursor(dy) }
      onActivateRequested: root.activate(root.rows[root.selectedIndex])
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) { if (t === "r" || t === "R") root.refresh() }

      Flickable {
        id: listScroll
        anchors.fill: parent
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

              readonly property bool isInstrument: modelData.type === "instrument"
              readonly property bool isAttribution: modelData.type === "attribution"
              readonly property bool cursorable: isInstrument || isAttribution
              readonly property bool hasCursor: cursorable && index === root.selectedIndex
              readonly property bool twoLine: isInstrument && !!modelData.detail
              readonly property color rowForeground: isInstrument && !modelData.valid
                ? root.mutedForeground : root.contentForeground

              width: contentColumn.width
              height: modelData.type === "sep" ? Style.space(11)
                : modelData.type === "header" ? headerLabel.implicitHeight + Style.space(8)
                : modelData.type === "note" ? noteColumn.implicitHeight + Style.space(12)
                : modelData.type === "footer" ? footerLabel.implicitHeight + Style.space(8)
                : twoLine ? Style.space(44) : Style.space(32)

              PanelSeparator {
                visible: rowItem.modelData.type === "sep"
                anchors.verticalCenter: parent.verticalCenter
                foreground: root.contentForeground
              }

              PanelSectionHeader {
                id: headerLabel
                visible: rowItem.modelData.type === "header"
                text: rowItem.modelData.type === "header" ? rowItem.modelData.label : ""
                foreground: root.contentForeground
                fontFamily: root.contentFontFamily
                anchors.bottom: parent.bottom
                anchors.bottomMargin: Style.space(2)
              }

              Column {
                id: noteColumn
                visible: rowItem.modelData.type === "note"
                width: parent.width - Style.space(16)
                x: Style.space(8)
                spacing: Style.space(3)
                anchors.verticalCenter: parent.verticalCenter

                Text {
                  width: parent.width
                  textFormat: Text.PlainText
                  text: rowItem.modelData.label || ""
                  color: rowItem.modelData.urgent ? (root.bar ? root.bar.urgent : Color.urgent) : root.contentForeground
                  font.family: root.contentFontFamily
                  font.pixelSize: Style.font.caption
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
                visible: rowItem.modelData.type === "footer"
                width: parent.width - Style.space(16)
                x: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                textFormat: Text.PlainText
                text: rowItem.modelData.type === "footer" ? rowItem.modelData.label : ""
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

                // ★ SYM · Name                         $64,210.00  ▲ +1.20%
                Row {
                  visible: rowItem.isInstrument
                  anchors.fill: parent
                  anchors.leftMargin: Style.space(8)
                  anchors.rightMargin: Style.space(8)
                  spacing: Style.space(8)

                  Text {
                    width: Style.space(14)
                    height: parent.height
                    textFormat: Text.PlainText
                    text: rowItem.modelData.favorite ? "★" : "☆"
                    color: rowItem.modelData.favorite ? root.contentForeground : root.mutedForeground
                    font.family: root.contentFontFamily
                    font.pixelSize: Style.font.body
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                  }

                  Column {
                    width: parent.width - Style.space(14) - valueColumn.width - parent.spacing * 2
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

                MouseArea {
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: rowItem.isAttribution ? Qt.PointingHandCursor : Qt.ArrowCursor
                  onPositionChanged: root.selectedIndex = rowItem.index
                  onClicked: root.activate(rowItem.modelData)
                }
              }
            }
          }
        }
      }
    }
  }
}
