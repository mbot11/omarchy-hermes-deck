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
  property string renamingId: ""    // session id whose title is being edited
  property string searchQuery: ""   // submitted search; drives the collector
  property string searchDraft: ""   // text currently in the search field
  readonly property bool showKanban: setting("showKanban", true) === true
  readonly property bool kanbanVisible: showKanban && service !== null && service.kanban.available === true
  readonly property bool showCron: setting("showCron", true) === true
  // Gated on the SETTING only. Whether there are jobs is a separate question —
  // folding it in here made the "No scheduled jobs" empty state unreachable,
  // because the section was hidden whenever the list was empty.
  readonly property bool cronVisible: showCron && service !== null
  readonly property var cronJobs: service !== null && Array.isArray(service.cron) ? service.cron : []
  // The header summary, computed once for the section rather than once per
  // repeater row (every row rendered the same string).
  readonly property string cronSummaryText: cronCountsSummary(cronJobs)

  readonly property var barIdentity: hostWidget || root
  readonly property var deck: service ? service.state : null
  readonly property string stateKind: DeckState.deckState(deck)
  readonly property var sessions: service ? service.sessions : []
  readonly property var usage: service ? service.usage : {}

  function display(value, fallback) {
    var text = String(value === undefined || value === null ? "" : value)
    return text === "" ? fallback : text
  }

  function beginRename(session) {
    if (!session)
      return
    root.renamingId = String(session.id || "")
    renameField.text = String(session.title || "")
    renameField.forceActiveFocus()
    renameField.selectAll()
  }

  function endRename() {
    root.renamingId = ""
    renameField.text = ""
  }

  function submitSearch() {
    var query = root.searchDraft.trim()
    root.searchQuery = query
    if (root.service)
      root.service.searchQuery = query
  }

  function clearSearch() {
    root.searchDraft = ""
    root.searchQuery = ""
    if (root.service)
      root.service.searchQuery = ""
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
      service.cronEnabled = root.showCron
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

          Row {
            spacing: Style.space(8)
            Text {
              text: "Hermes Deck"
              textFormat: Text.PlainText
              color: root.bar.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
            }
            Rectangle {
              visible: root.service !== null && root.service.isDefaultAgent === true
              radius: Style.space(3)
              color: Qt.rgba(root.bar.foreground.r, root.bar.foreground.g, root.bar.foreground.b, 0.12)
              border.width: 1
              border.color: "#4ec9d4"
              implicitWidth: defaultAgentText.implicitWidth + Style.space(8)
              implicitHeight: defaultAgentText.implicitHeight + Style.space(4)
              Text {
                id: defaultAgentText
                anchors.centerIn: parent
                text: "DEFAULT AGENT"
                textFormat: Text.PlainText
                color: "#4ec9d4"
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
              }
            }
          }

          Text {
            width: parent.width
            text: {
              if (!root.service)
                return "waiting for service"
              if (!root.service.installed)
                return "Not installed · omarchy install ai hermes"
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

          Button {
            visible: root.service !== null && root.service.desktopAvailable === true
            text: "Open desktop"
            foreground: root.bar.foreground
            fontFamily: root.bar.fontFamily
            bordered: true
            enabled: root.service !== null && root.service.busy === false
            onClicked: {
              root.runAction("launch-desktop")
              root.close()
            }
          }

          Button {
            visible: root.service !== null && root.service.installed === true && root.service.isDefaultAgent === false
            text: "Set as default"
            foreground: root.bar.foreground
            fontFamily: root.bar.fontFamily
            bordered: true
            enabled: root.service !== null && root.service.busy === false
            onClicked: root.runAction("set-default-agent")
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
          value: {
            if (!root.service)
              return "unknown"
            var base = root.display(root.service.gatewayState, "unknown")
            var platforms = root.service.connectedPlatforms
            if (platforms && platforms.length > 0)
              return base + " (" + platforms.join(", ") + ")"
            return base
          }
        }
        InfoPair {
          visible: root.service !== null && root.service.activeAgentsCount > 0
          label: "Active tasks"
          value: root.service ? root.service.activeAgentsCount + " running" : ""
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
            isLatest: modelData.id === DeckState.newestSessionId(root.sessions)
            onClicked: {
              if (root.service)
                root.service.act("resume-session", session.id)
              root.close()
            }
            onPinRequested: {
              if (root.service)
                root.service.act(session.pinned ? "unpin-session" : "pin-session", session.id)
            }
            onRenameRequested: root.beginRename(session)
          }
        }

        // Rename is inline: the field appears under the list, Enter commits
        // and Escape cancels. A title is free text, so it travels as a single
        // argv entry and deck-act gates only its length and emptiness.
        Row {
          visible: root.renamingId !== ""
          width: parent.width
          spacing: Style.space(6)

          Text {
            text: "rename:"
            textFormat: Text.PlainText
            anchors.verticalCenter: parent.verticalCenter
            color: Qt.darker(root.bar.foreground, 1.4)
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
          }

          TextInput {
            id: renameField
            width: parent.width - parent.children[0].implicitWidth - Style.space(6)
            height: Style.space(26)
            color: root.bar.foreground
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.bodySmall
            verticalAlignment: Text.AlignVCenter
            selectByMouse: true
            clip: true

            Rectangle {
              anchors.fill: parent
              radius: Style.space(3)
              color: "transparent"
              border.width: 1
              border.color: Qt.rgba(root.bar.foreground.r, root.bar.foreground.g, root.bar.foreground.b, 0.25)
              z: -1
            }

            onAccepted: {
              if (root.service && text.trim() !== "")
                root.service.act("rename-session", [root.renamingId, text.trim()])
              root.endRename()
            }
            Keys.onEscapePressed: root.endRename()
          }
        }
      }
      // ── Search ─────────────────────────────────────────────────────────
      Column {
        width: parent.width
        spacing: Style.space(6)

        PanelSectionHeader {
          text: "SEARCH"
          foreground: root.bar.foreground
          fontFamily: root.bar.fontFamily
        }

        Row {
          width: parent.width
          spacing: Style.space(6)

          TextInput {
            id: searchField
            width: parent.width - searchButton.implicitWidth - clearButton.implicitWidth - Style.space(12)
            height: Style.space(26)
            color: root.bar.foreground
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.bodySmall
            verticalAlignment: Text.AlignVCenter
            selectByMouse: true
            clip: true
            text: root.searchDraft
            onTextChanged: root.searchDraft = text

            Rectangle {
              anchors.fill: parent
              radius: Style.space(3)
              color: "transparent"
              border.width: 1
              border.color: Qt.rgba(root.bar.foreground.r, root.bar.foreground.g, root.bar.foreground.b, 0.25)
              z: -1
            }

            onAccepted: root.submitSearch()
            Keys.onEscapePressed: root.clearSearch()
          }

          Button {
            id: searchButton
            text: "Find"
            foreground: root.bar.foreground
            fontFamily: root.bar.fontFamily
            bordered: true
            enabled: root.service !== null && root.searchDraft.trim() !== ""
            onClicked: root.submitSearch()
          }

          Button {
            id: clearButton
            text: "Clear"
            foreground: root.bar.foreground
            fontFamily: root.bar.fontFamily
            bordered: true
            visible: root.searchQuery !== ""
            onClicked: root.clearSearch()
          }
        }

        Text {
          visible: root.searchQuery !== ""
          width: parent.width
          text: root.service && root.service.searchResults
            ? (root.service.searchResults.results.length + " conversation(s) matching \"" + root.searchQuery + "\"")
            : ""
          textFormat: Text.PlainText
          color: Qt.darker(root.bar.foreground, 1.4)
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }

        Repeater {
          model: root.service && root.service.searchResults && Array.isArray(root.service.searchResults.results)
            ? root.service.searchResults.results.slice(0, 8)
            : []

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
            onPinRequested: {
              if (root.service)
                root.service.act(session.pinned ? "unpin-session" : "pin-session", session.id)
            }
            onRenameRequested: root.beginRename(session)
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

      // ── Cron ───────────────────────────────────────────────────────────
      // Inventory only. This shows which scheduled jobs exist and which are
      // paused; it deliberately offers NO control. Pausing and resuming are not
      // wired as actions at all (deck-act has no cron case), so the CLI remains
      // the only place to change a job. An earlier comment claimed they were
      // reachable from the row, which was never true.
      Column {
        visible: root.cronVisible
        width: parent.width
        spacing: Style.space(6)

        Row {
          width: parent.width
          spacing: Style.space(8)

          PanelSectionHeader {
            text: "CRON"
            foreground: root.bar.foreground
            fontFamily: root.bar.fontFamily
          }
          Item {
            height: 1
            width: Math.max(0, parent.width - parent.children[0].implicitWidth
                            - parent.children[1].implicitWidth - Style.space(16))
          }
          Text {
            text: root.cronSummaryText
            textFormat: Text.PlainText
            color: Qt.darker(root.bar.foreground, 1.4)
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.bodySmall
          }
        }

        Repeater {
          model: root.cronVisible ? root.cronJobs.slice(0, 5) : []

          Row {
            required property var modelData
            width: parent.width
            spacing: Style.space(8)

            Text {
              text: modelData.paused ? "⏸" : "●"
              textFormat: Text.PlainText
              color: modelData.paused ? Qt.darker(root.bar.foreground, 1.5) : root.bar.foreground
              font.pixelSize: Style.font.body
            }
            Text {
              text: String(modelData.name || "job")
              textFormat: Text.PlainText
              color: root.bar.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
            Item {
              height: 1
              width: Math.max(0, parent.width - parent.children[0].implicitWidth - parent.children[1].implicitWidth - Style.space(16))
            }
            Text {
              // Per-row state, not the section summary: the summary used to be
              // rendered here, which repeated the same string on every row.
              text: modelData.paused ? "paused" : "active"
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
            return root.cronVisible && root.cronJobs.length === 0
          }
          width: parent.width
          text: "No scheduled jobs"
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

  // "2 jobs, 1 paused" — the paused count is the actionable half, so it is
  // always shown rather than only when non-zero.
  function cronCountsSummary(jobs) {
    var summary = DeckState.cronSummary(jobs)
    if (summary.total === 0)
      return ""
    var noun = summary.total === 1 ? "job" : "jobs"
    if (summary.paused === 0)
      return summary.total + " " + noun
    return summary.total + " " + noun + ", " + summary.paused + " paused"
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
    property bool isLatest: false
    signal activate()
    signal pinRequested()
    signal renameRequested()
    implicitHeight: sessionLayout.implicitHeight + Style.space(8)
    hoverEnabled: true
    onClicked: sessionRow.activate()

    // Right-click pins or unpins. Pinning is the flag Hermes Desktop's sidebar
    // reads, so this is the one action here that changes another surface.
    acceptedButtons: Qt.LeftButton | Qt.RightButton
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton)
        sessionRow.pinRequested()
    }

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

      // LATEST marks the newest conversation, which is a different fact from
      // the live dot: the dot is "working now", this is "most recent".
      Rectangle {
        visible: sessionRow.isLatest
        radius: Style.space(3)
        color: Qt.rgba(root.bar.foreground.r, root.bar.foreground.g, root.bar.foreground.b, 0.10)
        border.width: 1
        border.color: Qt.rgba(root.bar.foreground.r, root.bar.foreground.g, root.bar.foreground.b, 0.30)
        implicitWidth: latestText.implicitWidth + Style.space(8)
        implicitHeight: latestText.implicitHeight + Style.space(2)

        Text {
          id: latestText
          anchors.centerIn: parent
          text: "LATEST"
          textFormat: Text.PlainText
          color: root.bar.foreground
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
          font.bold: true
        }
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
            var badge = DeckState.platformBadge(sessionRow.session.source)
            if (badge && badge !== "CLI")
              bits.push("[" + badge + "]")
            bits.push(DeckState.relTime(sessionRow.session.lastActivityAt !== null && sessionRow.session.lastActivityAt !== undefined ? sessionRow.session.lastActivityAt : sessionRow.session.startedAt, Date.now()))
            if (sessionRow.session.workspace)
              bits.push(sessionRow.session.workspace)
            if (sessionRow.session.model)
              bits.push(DeckState.shortenModel(sessionRow.session.model))
            return bits.join(" · ")
          }
          // Session titles, workspace paths and model ids are written by Hermes
          // and by whatever ran in the session, so they are not ours to trust;
          // AutoText would read markup in them.
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
