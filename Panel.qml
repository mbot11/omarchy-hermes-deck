import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "js/DeckState.js" as DeckState

Panel {
  id: root
  moduleName: "io.github.mbot11.hermes-deck"
  ipcTarget: "io.github.mbot11.hermes-deck"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  property var service: null
  property string pluginDir: ""

  property string lastActionError: ""
  property string confirmAction: "" // armed destructive action, cleared by timer
  readonly property bool showKanban: setting("showKanban", true) === true
  readonly property bool kanbanVisible: showKanban && service !== null && service.kanban.available === true

  readonly property var barIdentity: hostWidget || root
  readonly property var deck: service ? service.state : null
  readonly property string stateKind: DeckState.deckState(deck)
  readonly property var sessions: service ? service.sessions : []
  readonly property var usage: service ? service.usage : {}

  function display(value, fallback) {
    var text = String(value === undefined || value === null ? "" : value)
    return text === "" ? fallback : text
  }

  function open() {
    root.controller.show()
    if (service)
      service.panelOpen = true
    if (service)
      service.refreshSlow()
  }

  function close() {
    if (service)
      service.panelOpen = false
    root.controller.hide()
  }

  function toggle() {
    if (root.opened)
      close()
    else
      open()
  }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  function pushSettings() {
    if (service) {
      service.notificationsEnabled = setting("notify", true) === true
      service.kanbanEnabled = root.showKanban
    }
  }

  onServiceChanged: pushSettings()
  Component.onCompleted: pushSettings()

  onConfirmActionChanged: {
    if (confirmAction !== "")
      confirmDisarm.restart()
  }

  Timer {
    id: confirmDisarm
    interval: 3000
    onTriggered: root.confirmAction = ""
  }

  // One deck-act at a time; destructive actions need a second click within
  // 3 seconds ("armed"). On completion, refresh state and surface failures.
  function runAction(name, arg, needsConfirm) {
    if (!service)
      return
    if (needsConfirm && root.confirmAction !== name) {
      root.confirmAction = name
      return
    }
    root.confirmAction = ""
    service.act(name, arg)
  }

  Connections {
    target: service
    function onActionDone(action, exitCode) {
      if (exitCode !== 0)
        root.lastActionError = action + " failed (" + exitCode + ")"
      else
        root.lastActionError = ""
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root
    bar: root.bar
    open: root.opened
    contentWidth: panel.fittedContentWidth(Style.space(400))
    contentHeight: panel.fittedContentHeight(content.implicitHeight)

    PanelKeyCatcher {
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) {
        root.switchPanel(direction)
      }
    }

    Column {
      id: content
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.top: parent.top
      spacing: Style.space(12)

      // ── Hero ───────────────────────────────────────────────────────────
      Row {
        width: parent.width
        spacing: Style.space(12)

        Image {
          source: Qt.resolvedUrl("assets/hermes-logo.png")
          sourceSize: Qt.size(Style.space(36), Style.space(36))
          width: Style.space(36)
          height: Style.space(36)
          fillMode: Image.PreserveAspectFit
          opacity: root.stateKind === "offline" ? 0.35 : 1
          mipmap: true
        }

        Column {
          width: parent.width - parent.children[0].implicitWidth - parent.spacing
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.spacing.labelGap

          Text {
            text: "Hermes Deck"
            textFormat: Text.PlainText
            color: root.bar.foreground
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.title
            font.bold: true
          }

          Text {
            width: parent.width
            text: {
              if (!root.service)
                return "waiting for service"
              if (!root.service.installed)
                return "Hermes not found on PATH"
              var bits = []
              if (root.service.version)
                bits.push(root.service.version.replace(/^Hermes Agent /, ""))
              if (root.service.model)
                bits.push(root.service.model)
              return bits.join(" · ") || "installed"
            }
            textFormat: Text.PlainText
            color: Qt.darker(root.bar.foreground, 1.4)
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
            elide: Text.ElideRight
          }
        }
      }

      // ── Paused banner ─────────────────────────────────────────────────
      Rectangle {
        visible: root.service !== null && root.service.paused
        width: parent.width
        radius: Style.space(4)
        color: "#e2a84b"
        opacity: 0.14

        implicitHeight: pausedRow.implicitHeight + Style.space(12)

        Row {
          id: pausedRow
          anchors.margins: Style.space(6)
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(8)

          Text {
            text: "⏸"
            textFormat: Text.PlainText
            color: root.bar.foreground
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.body
          }

          Text {
            width: parent.width - parent.children[0].implicitWidth - parent.spacing
            text: "Emergency stop engaged" + (root.deck && root.deck.reason ? " — " + root.deck.reason : "") + ". Cron, kanban dispatch, and new gateway turns are halted."
            textFormat: Text.PlainText
            color: root.bar.foreground
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }
        }
      }

      PanelSeparator {
        foreground: root.bar.foreground
      }

      // ── Actions ────────────────────────────────────────────────────────
      Column {
        width: parent.width
        spacing: Style.space(6)

        PanelSectionHeader {
          text: "ACTIONS"
          foreground: root.bar.foreground
          fontFamily: root.bar.fontFamily
        }

        Flow {
          width: parent.width
          spacing: Style.space(6)

          Button {
            text: root.service && root.service.busy ? "…" : (root.service && root.service.paused ? "Resume agent" : "Pause agent")
            foreground: root.bar.foreground
            fontFamily: root.bar.fontFamily
            bordered: true
            enabled: root.service !== null && root.service.busy === false && root.service.installed === true
            onClicked: {
              if (root.service && root.service.paused)
                root.runAction("resume")
              else
                root.runAction("pause", "", true)
            }
          }

          Button {
            text: root.confirmAction === "gateway-restart" ? "Confirm restart?" : "Restart gateway"
            foreground: root.bar.foreground
            fontFamily: root.bar.fontFamily
            bordered: true
            enabled: root.service !== null && root.service.busy === false && root.service.installed === true
            onClicked: root.runAction("gateway-restart", "", true)
          }

          Button {
            text: "New chat"
            foreground: root.bar.foreground
            fontFamily: root.bar.fontFamily
            bordered: true
            enabled: root.service !== null && root.service.installed === true
            onClicked: {
              root.runAction("new-session")
              root.close()
            }
          }

          Button {
            text: "TUI modal"
            foreground: root.bar.foreground
            fontFamily: root.bar.fontFamily
            bordered: true
            onClicked: {
              if (root.service)
                root.service.toggleModal()
              root.close()
            }
          }
        }

        Text {
          visible: root.lastActionError !== ""
          text: root.lastActionError
          textFormat: Text.PlainText
          color: root.bar.urgent
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.bodySmall
        }
      }

      PanelSeparator {
        foreground: root.bar.foreground
      }

      // ── Status ─────────────────────────────────────────────────────────
      Column {
        width: parent.width
        spacing: Style.space(8)

        InfoPair {
          label: "Gateway"
          value: root.display(root.service && root.service.gatewayState, "unknown")
        }
        InfoPair {
          label: "Active model"
          value: root.display(root.service && root.service.model, "unknown")
        }
        InfoPair {
          label: "Providers"
          value: {
            if (!root.service)
              return "unknown"
            var auth = root.service.state.auth || []
            return auth.length ? auth.length + " configured" : "none detected"
          }
        }
      }

      PanelSeparator {
        foreground: root.bar.foreground
      }


      // ── Usage ──────────────────────────────────────────────────────────
      Column {
        width: parent.width
        spacing: Style.space(6)

        PanelSectionHeader {
          text: "USAGE"
          foreground: root.bar.foreground
          fontFamily: root.bar.fontFamily
        }

        Row {
          width: parent.width
          spacing: Style.space(16)

          UsageCell {
            width: (parent.width - Style.space(16)) / 2
            label: "Today"
            tokens: root.usage && root.usage.today ? root.usage.today.tokens || 0 : 0
            cost: root.usage && root.usage.today ? root.usage.today.costUsd || 0 : 0
            bar: root.bar
          }

          UsageCell {
            width: (parent.width - Style.space(16)) / 2
            label: "7 days"
            tokens: root.usage && root.usage.week ? root.usage.week.tokens || 0 : 0
            cost: root.usage && root.usage.week ? root.usage.week.costUsd || 0 : 0
            bar: root.bar
          }
        }

        Repeater {
          model: root.usage && Array.isArray(root.usage.byModelToday) ? root.usage.byModelToday.slice(0, 4) : []

          Row {
            required property var modelData
            width: parent.width
            spacing: Style.space(8)

            Text {
              text: "·"
              textFormat: Text.PlainText
              color: root.bar.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
            Text {
              text: DeckState.shortenModel(modelData.model)
              textFormat: Text.PlainText
              color: root.bar.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
            Item {
              height: 1
              width: Math.max(0, parent.width - parent.children[0].implicitWidth - parent.children[1].implicitWidth - parent.children[3].implicitWidth - Style.space(16))
            }
            Text {
              text: DeckState.fmtTokens(modelData.tokens) + (DeckState.fmtCost(modelData.costUsd) ? " · " + DeckState.fmtCost(modelData.costUsd) : "")
              textFormat: Text.PlainText
              color: Qt.darker(root.bar.foreground, 1.4)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
          }
        }
      }

      PanelSeparator {
        foreground: root.bar.foreground
      }

      // ── Sessions ───────────────────────────────────────────────────────
      Column {
        width: parent.width
        spacing: Style.space(6)

        PanelSectionHeader {
          text: "SESSIONS"
          foreground: root.bar.foreground
          fontFamily: root.bar.fontFamily
        }

        Text {
          visible: root.sessions.length === 0
          width: parent.width
          text: root.service && root.service.installed ? "No sessions yet" : "—"
          textFormat: Text.PlainText
          color: Qt.darker(root.bar.foreground, 1.4)
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.bodySmall
        }

        Repeater {
          model: root.sessions.slice(0, 5)

          SessionRow {
            required property var modelData
            width: parent.width
            session: modelData
            bar: root.bar
            onClicked: {
              if (root.service)
                root.service.act("resume-session", session.id)
              root.close()
            }
          }
        }
      }
      // ── Kanban ─────────────────────────────────────────────────────────
      Column {
        visible: root.kanbanVisible
        width: parent.width
        spacing: Style.space(6)

        PanelSectionHeader {
          text: "KANBAN"
          foreground: root.bar.foreground
          fontFamily: root.bar.fontFamily
        }

        Repeater {
          model: root.kanbanVisible && Array.isArray(service.kanban.boards) ? service.kanban.boards.slice(0, 5) : []

          Row {
            required property var modelData
            width: parent.width
            spacing: Style.space(8)

            Text {
              text: modelData.isCurrent ? "●" : "○"
              textFormat: Text.PlainText
              color: root.bar.foreground
              font.pixelSize: Style.font.body
            }
            Text {
              text: String(modelData.name || modelData.slug || "board")
              textFormat: Text.PlainText
              color: root.bar.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
            Item {
              height: 1
              width: Math.max(0, parent.width - parent.children[0].implicitWidth - parent.children[1].implicitWidth - parent.children[3].implicitWidth - Style.space(24))
            }
            Text {
              text: kanbanCountsSummary(modelData)
              textFormat: Text.PlainText
              color: Qt.darker(root.bar.foreground, 1.4)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
          }
        }

        Text {
          visible: {
            if (!root.service)
              return false
            var k = root.service.kanban
            return root.showKanban && (!k || !k.available)
          }
          width: parent.width
          text: "No kanban boards"
          textFormat: Text.PlainText
          color: Qt.darker(root.bar.foreground, 1.6)
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.bodySmall
        }
      }

      // ── Footer ─────────────────────────────────────────────────────────
      Column {
        width: parent.width
        spacing: Style.space(4)

        Text {
          visible: root.service !== null && root.service.lastError !== ""
          width: parent.width
          text: root.service ? root.service.lastError : ""
          textFormat: Text.PlainText
          color: root.bar.urgent
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.bodySmall
        }

        Text {
          visible: root.deck !== null && Array.isArray(root.deck.errors) && root.deck.errors.length > 0
          width: parent.width
          text: root.deck && root.deck.errors && root.deck.errors.length > 0 ? root.deck.errors[0] : ""
          textFormat: Text.PlainText
          color: Qt.darker(root.bar.foreground, 1.5)
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }

        Text {
          width: parent.width
          text: "left: panel · middle: refresh · right: TUI modal"
          textFormat: Text.PlainText
          color: Qt.darker(root.bar.foreground, 1.7)
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
        }
      }
    }
  }

  function kanbanCountsSummary(board) {
    var counts = board && board.counts ? board.counts : {}
    var parts = []
    var keys = Object.keys(counts)
    for (var i = 0; i < keys.length && parts.length < 3; i++) {
      var value = Number(counts[keys[i]] || 0)
      if (value > 0)
        parts.push(keys[i] + " " + value)
    }
    return parts.length ? parts.join(", ") : String(board && board.total ? board.total : 0)
  }

  component InfoPair: Row {
    property string label: ""
    property string value: ""
    width: parent.width
    spacing: Style.space(8)
    Text {
      text: label
      textFormat: Text.PlainText
      color: Qt.darker(root.bar.foreground, 1.35)
      font.family: root.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
    Item {
      width: Math.max(0, parent.width - parent.children[0].implicitWidth - parent.children[2].implicitWidth - Style.space(16))
      height: 1
    }
    Text {
      text: value
      textFormat: Text.PlainText
      color: root.bar.foreground
      font.family: root.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
      elide: Text.ElideLeft
      width: Math.min(implicitWidth, parent.width * 0.65)
    }
  }

  component UsageCell: Column {
    property string label: ""
    property real tokens: 0
    property real cost: 0
    property var bar: null
    spacing: Style.spacing.labelGap

    Text {
      text: label
      textFormat: Text.PlainText
      color: Qt.darker(root.bar.foreground, 1.35)
      font.family: root.bar.fontFamily
      font.pixelSize: Style.font.caption
    }
    Text {
      text: DeckState.fmtTokens(tokens)
      textFormat: Text.PlainText
      color: root.bar.foreground
      font.family: root.bar.fontFamily
      font.pixelSize: Style.font.title
      font.bold: true
    }
    Text {
      text: DeckState.fmtCost(cost) ? DeckState.fmtCost(cost) + " est." : "—"
      textFormat: Text.PlainText
      color: Qt.darker(root.bar.foreground, 1.4)
      font.family: root.bar.fontFamily
      font.pixelSize: Style.font.caption
    }
  }

  component SessionRow: MouseArea {
    id: sessionRow
    property var session: null
    property var bar: null
    signal activate()
    implicitHeight: sessionLayout.implicitHeight + Style.space(8)
    hoverEnabled: true
    onClicked: sessionRow.activate()

    Rectangle {
      anchors.fill: parent
      radius: Style.space(3)
      color: sessionRow.containsMouse ? Qt.rgba(root.bar.foreground.r, root.bar.foreground.g, root.bar.foreground.b, 0.08) : "transparent"
    }

    Row {
      id: sessionLayout
      anchors.margins: Style.space(4)
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(8)

      Text {
        text: sessionRow.session && sessionRow.session.pinned ? "★" : (sessionRow.session && sessionRow.session.source && sessionRow.session.source !== "cli" ? "◈" : "·")
        textFormat: Text.PlainText
        color: root.bar.foreground
        font.pixelSize: Style.font.body
      }

      Column {
        width: sessionRow.width - parent.children[0].implicitWidth - Style.space(8)
        spacing: 0

        Text {
          width: parent.width
          text: sessionRow.session ? (sessionRow.session.title || sessionRow.session.id || "untitled") : ""
          textFormat: Text.PlainText
          color: root.bar.foreground
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }

        Text {
          width: parent.width
          text: {
            if (!sessionRow.session)
              return ""
            var bits = []
            bits.push(DeckState.relTime(sessionRow.session.lastActivityAt !== null && sessionRow.session.lastActivityAt !== undefined ? sessionRow.session.lastActivityAt : sessionRow.session.startedAt, Date.now()))
            if (sessionRow.session.workspace)
              bits.push(sessionRow.session.workspace)
            if (sessionRow.session.model)
              bits.push(DeckState.shortenModel(sessionRow.session.model))
            return bits.join(" · ")
          }
          textFormat: Text.PlainText
          color: Qt.darker(root.bar.foreground, 1.4)
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }
    }
  }
}
