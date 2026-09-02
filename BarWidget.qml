import QtQuick
import qs.Ui

// Placeholder so the manifest validates before the real widget lands.
// The ticker strip, the panel and the IPC surface are built in session 2
// (see AGENTS.md → Roadmap). Do not enable the plugin on this version.
BarWidget {
  id: root
  moduleName: "costafot.markets"

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: ""
    tooltipText: "Markets — the widget arrives in the next version"
  }
}
