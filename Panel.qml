pragma ComponentBehavior: Bound
import QtQuick
import qs.Commons
import qs.Ui

// The bar popup for Markets: the kit's Panel and KeyboardPanel around the
// pages (Pages.qml), anchored under the strip, sized to the page under a
// cap. The IPC target lives in the window root (Window.qml, one per shell),
// so `manageIpc` stays off here. The bar tracks the widget mounted in its
// slot (BarWidget.qml), so the popout coordinator and panel switching
// identify us by that widget (`barIdentity`). The window shows the same
// pages as a toplevel.
Panel {
  id: root
  moduleName: "costafot.markets"

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root
  // The strip reads the store off us; the window has a store of its own.
  readonly property var store: pages.store
  // The page on screen, for `status`.
  readonly property string page: pages.page

  function showPage(name) { pages.showPage(name) }
  function refresh() { pages.refresh() }
  function addSymbol(symbol, category) { pages.addSymbol(symbol, category) }
  function favoriteSymbol(spec) { pages.favoriteSymbol(spec) }
  function chartStatus() { return pages.chartStatus() }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: pages.focusTarget
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(pages.desiredHeight, Style.space(760))

    Pages {
      id: pages
      anchors.fill: parent
      settings: root.settings
      shell: root.bar ? root.bar.shell : null
      hostWidget: root.hostWidget
      opened: root.opened
      inPopup: true
      contentForeground: root.bar ? root.bar.foreground : Color.foreground
      contentFontFamily: root.bar ? root.bar.fontFamily : Style.font.family
      urgentForeground: root.bar ? root.bar.urgent : Color.urgent
      foregroundAnimationEnabled: !root.bar || root.bar.foregroundAnimationEnabled
      onCloseRequested: root.close()
      onOpenRequested: root.open()
      onSwitchRequested: function(direction) { root.switchPanel(direction) }
    }
  }
}
