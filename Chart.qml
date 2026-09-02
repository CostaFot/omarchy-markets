pragma ComponentBehavior: Bound
import QtQuick
import qs.Commons

// The price chart on an instrument's page: a Canvas port of the Windows
// extension's ChartHelper (quarter gridlines, a gradient under the line,
// the line coloured by the range's direction) plus what its SVG surface
// could not draw: min and max labels at the right edge, first and last
// stamps under the plot, and a dashed previous-close line on the day
// chart. x is proportional to time, not to the point index, so a weekend
// gap on the week chart reads as a gap.
//
// Geometry lives here; every string comes formatted from the helper. The
// component never formats a number or a date.
Item {
  id: chart

  // [[unix_seconds, close], ...], oldest first (series.points).
  property var points: []
  // NaN when the helper had none; drawn only when it lies inside the plot.
  property real previousClose: NaN
  property bool up: true
  property color upColor: "#40a02b"
  property color downColor: "#d20f39"
  property color foreground: Color.foreground
  property color mutedForeground: Qt.darker(foreground, 1.4)
  // The popup's ground, for the label backings.
  property color background: Color.popups.background
  property string fontFamily: Style.font.family
  property string minText: ""
  property string maxText: ""
  property string previousCloseText: ""
  property string firstLabel: ""
  property string lastLabel: ""
  // Painted on top of the plot while a range loads over a prior chart.
  property bool loading: false

  readonly property color lineColor: up ? upColor : downColor
  readonly property real pad: Style.spaceReal(6)
  readonly property real plotHeight: Math.round(width / 3)
  readonly property real stampsHeight: firstLabel !== "" || lastLabel !== "" ? stampRow.implicitHeight + Style.space(2) : 0

  implicitHeight: plotHeight + stampsHeight

  // ---- Geometry, shared by the canvas and the labels ---------------------
  readonly property var bounds: {
    var pts = points || []
    if (pts.length === 0) return null
    var lo = Infinity, hi = -Infinity, t0 = Infinity, t1 = -Infinity
    for (var i = 0; i < pts.length; i++) {
      var t = Number(pts[i][0]), c = Number(pts[i][1])
      if (!isFinite(t) || !isFinite(c)) continue
      if (c < lo) lo = c
      if (c > hi) hi = c
      if (t < t0) t0 = t
      if (t > t1) t1 = t
    }
    if (!isFinite(lo)) return null
    return { lo: lo, hi: hi, t0: t0, t1: t1 }
  }

  function yFor(value) {
    var b = bounds
    var innerH = plotHeight - 2 * pad
    if (!b) return pad + innerH / 2
    var span = b.hi - b.lo
    // 0 (= min) at the bottom, 1 (= max) at the top; a flat series rides mid-height.
    var norm = span === 0 ? 0.5 : (value - b.lo) / span
    return pad + innerH * (1 - norm)
  }

  function xFor(t) {
    var b = bounds
    var innerW = width - 2 * pad
    if (!b || b.t1 === b.t0) return pad + innerW / 2
    return pad + innerW * (t - b.t0) / (b.t1 - b.t0)
  }

  readonly property bool showPrevious: {
    var b = bounds
    return b !== null && isFinite(previousClose) && previousClose >= b.lo && previousClose <= b.hi && b.hi !== b.lo
  }

  onPointsChanged: canvas.requestPaint()
  onPreviousCloseChanged: canvas.requestPaint()
  onUpChanged: canvas.requestPaint()
  onUpColorChanged: canvas.requestPaint()
  onDownColorChanged: canvas.requestPaint()
  onForegroundChanged: canvas.requestPaint()
  onWidthChanged: canvas.requestPaint()

  Canvas {
    id: canvas
    width: chart.width
    height: chart.plotHeight
    antialiasing: true

    function rgba(c, a) {
      return Qt.rgba(c.r, c.g, c.b, a)
    }

    onPaint: {
      var ctx = getContext("2d")
      ctx.reset()
      ctx.clearRect(0, 0, width, height)
      var pad = chart.pad
      var innerW = width - 2 * pad
      var innerH = height - 2 * pad
      if (innerW <= 0 || innerH <= 0) return

      // Quarter gridlines first, so the line and fill draw over them.
      ctx.lineWidth = 1
      ctx.strokeStyle = rgba(chart.foreground, 0.18)
      var fractions = [0, 0.25, 0.5, 0.75, 1]
      ctx.beginPath()
      for (var g = 0; g < fractions.length; g++) {
        var y = Math.round(pad + innerH * fractions[g]) + 0.5
        ctx.moveTo(pad, y)
        ctx.lineTo(width - pad, y)
        var x = Math.round(pad + innerW * fractions[g]) + 0.5
        ctx.moveTo(x, pad)
        ctx.lineTo(x, height - pad)
      }
      ctx.stroke()

      var pts = chart.points || []
      if (!chart.bounds || pts.length === 0) return

      var xs = [], ys = []
      for (var i = 0; i < pts.length; i++) {
        var t = Number(pts[i][0]), c = Number(pts[i][1])
        if (!isFinite(t) || !isFinite(c)) continue
        xs.push(chart.xFor(t))
        ys.push(chart.yFor(c))
      }
      if (xs.length === 0) return

      // Gradient under the line, closed down to the baseline.
      var grad = ctx.createLinearGradient(0, pad, 0, pad + innerH)
      grad.addColorStop(0, rgba(chart.lineColor, 0.35))
      grad.addColorStop(1, rgba(chart.lineColor, 0.02))
      ctx.beginPath()
      ctx.moveTo(xs[0], ys[0])
      for (var f = 1; f < xs.length; f++) ctx.lineTo(xs[f], ys[f])
      ctx.lineTo(xs[xs.length - 1], height - pad)
      ctx.lineTo(xs[0], height - pad)
      ctx.closePath()
      ctx.fillStyle = grad
      ctx.fill()

      // Yesterday's close, dashed, when it lies inside the plot.
      if (chart.showPrevious) {
        var py = Math.round(chart.yFor(chart.previousClose)) + 0.5
        ctx.save()
        ctx.setLineDash([4, 4])
        ctx.lineWidth = 1
        ctx.strokeStyle = rgba(chart.foreground, 0.45)
        ctx.beginPath()
        ctx.moveTo(pad, py)
        ctx.lineTo(width - pad, py)
        ctx.stroke()
        ctx.restore()
      }

      // The price line; a lone point is a dot at centre.
      ctx.strokeStyle = chart.lineColor
      ctx.fillStyle = chart.lineColor
      ctx.lineWidth = 2
      ctx.lineJoin = "round"
      ctx.lineCap = "round"
      if (xs.length === 1) {
        ctx.beginPath()
        ctx.arc(xs[0], ys[0], 3, 0, Math.PI * 2)
        ctx.fill()
      } else {
        ctx.beginPath()
        ctx.moveTo(xs[0], ys[0])
        for (var l = 1; l < xs.length; l++) ctx.lineTo(xs[l], ys[l])
        ctx.stroke()
      }
    }
  }

  // Max at the top right, min at the bottom right, inside the plot frame.
  // Labels sit on a translucent backing so they read over the line (the
  // max label is usually exactly where the line peaks).
  component PlotLabel: Rectangle {
    property alias text: labelText.text
    implicitWidth: labelText.implicitWidth + Style.space(6)
    implicitHeight: labelText.implicitHeight + Style.space(2)
    radius: Style.space(3)
    color: Qt.rgba(chart.background.r, chart.background.g, chart.background.b, 0.72)

    Text {
      id: labelText
      anchors.centerIn: parent
      textFormat: Text.PlainText
      color: chart.mutedForeground
      font.family: chart.fontFamily
      font.pixelSize: Style.font.caption
    }
  }

  PlotLabel {
    visible: chart.maxText !== "" && chart.bounds !== null
    anchors.right: parent.right
    anchors.rightMargin: chart.pad + Style.space(2)
    y: chart.pad + Style.space(1)
    text: chart.maxText
  }

  PlotLabel {
    visible: chart.minText !== "" && chart.bounds !== null
    anchors.right: parent.right
    anchors.rightMargin: chart.pad + Style.space(2)
    y: chart.plotHeight - chart.pad - implicitHeight - Style.space(1)
    text: chart.minText
  }

  // The previous-close label sits just above its line, at the left edge.
  PlotLabel {
    visible: chart.showPrevious && chart.previousCloseText !== ""
    x: chart.pad + Style.space(2)
    y: Math.min(chart.plotHeight - chart.pad - implicitHeight, Math.max(chart.pad, chart.yFor(chart.previousClose) - implicitHeight - Style.space(1)))
    text: chart.previousCloseText
  }

  Text {
    visible: chart.loading
    anchors.centerIn: canvas
    textFormat: Text.PlainText
    text: "…"
    color: chart.mutedForeground
    font.family: chart.fontFamily
    font.pixelSize: Style.font.heading
  }

  // First and last stamps under the plot.
  Item {
    id: stampRow
    anchors.top: canvas.bottom
    anchors.topMargin: Style.space(2)
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.leftMargin: chart.pad
    anchors.rightMargin: chart.pad
    implicitHeight: Math.max(firstStamp.implicitHeight, lastStamp.implicitHeight)
    visible: chart.stampsHeight > 0

    Text {
      id: firstStamp
      anchors.left: parent.left
      textFormat: Text.PlainText
      text: chart.firstLabel
      color: chart.mutedForeground
      font.family: chart.fontFamily
      font.pixelSize: Style.font.caption
    }

    Text {
      id: lastStamp
      anchors.right: parent.right
      textFormat: Text.PlainText
      text: chart.lastLabel
      color: chart.mutedForeground
      font.family: chart.fontFamily
      font.pixelSize: Style.font.caption
    }
  }
}
