import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "js/DeckState.js" as DeckState

BarWidget {
  id: root
  moduleName: "io.github.mbot11.hermes-deck"

  property var shell: null
  property var service: null

  DeckAdapter {
    id: adapter
  }

  function refreshService() {
    service = adapter.findService(root.bar, root.shell, null)
  }

  readonly property var deckState: service ? service.state : null
  readonly property string stateKind: DeckState.deckState(deckState)
  readonly property bool working: stateKind === "working"
  readonly property bool paused: stateKind === "paused"
  readonly property bool broken: stateKind === "gatewayDown"
  readonly property bool offline: stateKind === "offline"

  readonly property string stateColor: {
    if (paused)
      return "#e2a84b" // amber: explicit human-halt signal
    if (broken)
      return root.bar && root.bar.urgent ? root.bar.urgent : "#e25c5c"
    if (working)
      return "#4ec9d4" // cyan: agent executing
    if (offline)
      return Qt.darker(root.bar.foreground, 1.8)
    return root.bar.foreground
  }

  readonly property string pillText: DeckState.pillFor(deckState)
  readonly property string tooltip: {
    if (!service)
      return "Hermes Deck — waiting for service"
    if (offline)
      return "Hermes Deck — Hermes not found"
    var bits = []
    if (paused)
      bits.push("PAUSED" + (deckState.reason ? " — " + deckState.reason : ""))
    if (broken)
      bits.push("gateway " + service.gatewayState)
    if (service.model)
      bits.push(service.model)
    if (service.working)
      bits.push("working")
    return "Hermes Deck — " + (bits.length ? bits.join(" · ") : "idle")
  }

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false

  function open() {
    if (panelLoader.item)
      panelLoader.item.open()
  }

  function close() {
    if (panelLoader.item)
      panelLoader.item.close()
  }

  function toggle() {
    if (panelLoader.item)
      panelLoader.item.toggle()
  }

  function closeForPopoutSwitch() {
    if (panelLoader.item)
      panelLoader.item.closeForPopoutSwitch()
  }

  function injectPanel() {
    var target = panelLoader.item
    if (!target)
      return
    if ("bar" in target)
      target.bar = root.bar
    if ("anchorItem" in target)
      target.anchorItem = button
    if ("hostWidget" in target)
      target.hostWidget = root
    if ("service" in target)
      target.service = root.service
    if ("pluginDir" in target)
      target.pluginDir = root.service && root.service.pluginDir ? root.service.pluginDir : adapter.localPluginDir()
  }

  implicitWidth: row.implicitWidth
  implicitHeight: row.implicitHeight

  onBarChanged: {
    refreshService()
    injectPanel()
  }
  onServiceChanged: injectPanel()

  Component.onCompleted: refreshService()

  // _services is populated asynchronously after widget construction.
  Timer {
    interval: 400
    running: root.service === null
    repeat: true
    onTriggered: root.refreshService()
  }

  Connections {
    target: root.service
    function onWorkingChanged() {
      if (root.working)
        pulseAnim.restart()
    }
  }

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

  Row {
    id: row
    spacing: Style.space(4)

    WidgetButton {
      id: button
      bar: root.bar
      text: "⚕"
      labelVisible: false
      tooltipText: root.tooltip
      onPressed: function(buttonCode) {
        if (buttonCode === Qt.LeftButton)
          root.toggle()
        else if (buttonCode === Qt.MiddleButton) {
          if (root.service)
            root.service.refreshSlow()
        } else if (buttonCode === Qt.RightButton) {
          if (root.service)
            root.service.toggleModal()
        }
      }
      Image {
        anchors.centerIn: parent
        source: Qt.resolvedUrl("assets/hermes-logo.png")
        sourceSize: Qt.size(20, 20)
        width: 20
        height: 20
        fillMode: Image.PreserveAspectFit
        opacity: root.offline ? 0.35 : 1
        mipmap: true
      }

      Text {
        visible: root.pillText !== ""
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.margins: -2
        text: root.pillText
        textFormat: Text.PlainText
        color: root.stateColor
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.caption
      }

      Rectangle {
        id: pulseHalo
        anchors.fill: parent
        anchors.margins: -2
        radius: height / 2
        color: "transparent"
        border.width: 2
        border.color: root.stateColor
        opacity: root.working ? haloPulse.pulseOpacity : 0
        property real pulseOpacity: root.working ? 1 : 0
        Behavior on border.color {
          ColorAnimation {
            duration: 180
          }
        }
      }

      SequentialAnimation {
        id: haloPulse
        running: root.working
        loops: Animation.Infinite
        NumberAnimation {
          target: pulseHalo
          property: "pulseOpacity"
          from: 1
          to: 0.2
          duration: 900
        }
        NumberAnimation {
          target: pulseHalo
          property: "pulseOpacity"
          from: 0.2
          to: 1
          duration: 900
        }
      }
    }
  }
}
