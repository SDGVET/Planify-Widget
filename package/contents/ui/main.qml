import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as Plasma5Support
import org.kde.plasma.plasmoid

PlasmoidItem {
    id: root

    readonly property bool inPanel: Plasmoid.formFactor === PlasmaCore.Types.Horizontal
        || Plasmoid.formFactor === PlasmaCore.Types.Vertical
        || [PlasmaCore.Types.TopEdge, PlasmaCore.Types.RightEdge,
            PlasmaCore.Types.BottomEdge, PlasmaCore.Types.LeftEdge].includes(Plasmoid.location)

    // Last good data. A failed refresh never replaces these.
    property var tasks: []
    property var counts: ({ overdue: 0, today: 0, soon: 0, important: 0 })
    property var syncInfo: ({ last_sync: null, age_minutes: null, stale: false, planify_running: null })
    property bool hasData: false
    property string tasksKey: ""
    property date lastSuccess: new Date()
    property string lastError: ""

    // Run state.
    property bool busy: false
    property bool rerunPending: false
    property bool configDirty: false
    property string currentSource: ""
    property int runNumber: 0
    property string dayKey: currentDayKey()

    readonly property int soonDays: clampInt(Plasmoid.configuration.soonDays, 1, 30, 3)
    readonly property int minPriority: [2, 3, 4].includes(Plasmoid.configuration.minPriority)
        ? Plasmoid.configuration.minPriority : 4
    readonly property int staleMinutes: clampInt(Plasmoid.configuration.staleMinutes, 5, 1440, 60)
    readonly property string readerArgs: "--soon-days " + soonDays
        + " --min-priority " + minPriority
        + " --stale-minutes " + staleMinutes

    readonly property var visibleGroups: inPanel
        ? ["overdue", "today"]
        : ["overdue", "today", "soon", "important"]
    readonly property var rows: buildRows(tasks, counts, visibleGroups, inPanel)
    readonly property bool hasVisibleTasks: rows.some(entry => entry.kind === "task")
    readonly property string staleText: syncInfo.stale === true ? staleMessage(syncInfo.age_minutes) : ""

    Plasmoid.icon: "view-task"
    preferredRepresentation: inPanel ? compactRepresentation : fullRepresentation
    toolTipMainText: i18n("Planify Tasks")
    toolTipSubText: {
        if (!hasData) {
            return lastError.length > 0 ? i18n("Could not read tasks") : i18n("Loading tasks…");
        }
        return i18n("Today: %1, Overdue: %2", counts.today, counts.overdue);
    }

    Plasmoid.contextualActions: [
        PlasmaCore.Action {
            text: i18n("Open Planify")
            icon.name: "view-task"
            onTriggered: root.openPlanify()
        }
    ]

    function clampInt(value, low, high, fallback) {
        const number = Number(value);
        return Number.isInteger(number) ? Math.min(high, Math.max(low, number)) : fallback;
    }

    function countOf(value) {
        const number = Number(value);
        return Number.isInteger(number) && number > 0 ? number : 0;
    }

    function currentDayKey() {
        const now = new Date();
        return now.getFullYear() + "-" + now.getMonth() + "-" + now.getDate();
    }

    function shellQuote(value) {
        return "'" + String(value).replace(/'/g, "'\\''") + "'";
    }

    function readerPath() {
        const url = Qt.resolvedUrl("../code/read_tasks.py").toString();
        return decodeURIComponent(url.replace(/^file:\/\//, ""));
    }

    // The run number keeps each source name unique. timeout stops a hung reader.
    function readerCommand() {
        return "PLANIFY_WIDGET_RUN=" + runNumber
            + " timeout -k 2 25 python3 -I " + shellQuote(readerPath())
            + " " + readerArgs;
    }

    function refresh() {
        if (busy) {
            rerunPending = true;
            return;
        }
        rerunPending = false;
        configDirty = false;
        runNumber += 1;
        currentSource = readerCommand();
        busy = true;
        watchdog.restart();
        reader.connectSource(currentSource);
    }

    function finishRun(data) {
        watchdog.stop();
        busy = false;
        currentSource = "";
        if (configDirty) {
            // This result used the previous settings.
            refresh();
            return;
        }
        applyResult(data);
        if (rerunPending) {
            refresh();
        }
    }

    function applyResult(data) {
        let parsed = null;
        try {
            parsed = JSON.parse(String(data.stdout ?? ""));
        } catch (error) {
            lastError = i18n("The task reader returned invalid data.");
            return;
        }
        if (parsed === null || typeof parsed !== "object") {
            lastError = i18n("The task reader returned invalid data.");
            return;
        }
        if (parsed.ok !== true) {
            lastError = typeof parsed.error === "string" && parsed.error.length > 0
                ? parsed.error
                : i18n("The task reader reported an error.");
            return;
        }
        if (!Array.isArray(parsed.tasks) || parsed.counts === null || typeof parsed.counts !== "object") {
            lastError = i18n("The task reader returned invalid data.");
            return;
        }

        const nextTasks = parsed.tasks.filter(task => task !== null && typeof task === "object");
        const nextCounts = {
            overdue: countOf(parsed.counts.overdue),
            today: countOf(parsed.counts.today),
            soon: countOf(parsed.counts.soon),
            important: countOf(parsed.counts.important),
        };
        // Reassign the model only on change, so a refresh does not reset scrolling.
        const nextKey = JSON.stringify([nextTasks, nextCounts]);
        if (nextKey !== tasksKey) {
            tasksKey = nextKey;
            counts = nextCounts;
            tasks = nextTasks;
        }
        const nextSync = parsed.sync !== null && typeof parsed.sync === "object" ? parsed.sync : {};
        syncInfo = {
            last_sync: nextSync.last_sync ?? null,
            age_minutes: Number.isInteger(nextSync.age_minutes) ? nextSync.age_minutes : null,
            stale: nextSync.stale === true,
            planify_running: typeof nextSync.planify_running === "boolean" ? nextSync.planify_running : null,
        };
        lastSuccess = new Date();
        lastError = "";
        hasData = true;
    }

    function groupTitle(group) {
        switch (group) {
        case "overdue": return i18n("Overdue");
        case "today": return i18n("Today");
        case "soon": return i18n("Due soon");
        default: return i18n("Important");
        }
    }

    // Tasks stay in reader order. Grouping comes from the reader's "group" field.
    function buildRows(taskList, taskCounts, groups, hideEmptyGroups) {
        const result = [];
        groups.forEach(group => {
            const members = taskList.filter(task => task.group === group);
            if (hideEmptyGroups && members.length === 0) {
                return;
            }
            result.push({ kind: "header", group: group, title: groupTitle(group), count: taskCounts[group] });
            members.forEach(task => result.push({ kind: "task", group: group, task: task }));
        });
        return result;
    }

    function formatAge(minutes) {
        if (minutes < 60) {
            return i18nc("Duration in minutes", "%1m", minutes);
        }
        if (minutes < 1440) {
            return i18nc("Duration in hours", "%1h", Math.floor(minutes / 60));
        }
        return i18nc("Duration in days", "%1d", Math.floor(minutes / 1440));
    }

    function staleMessage(ageMinutes) {
        if (ageMinutes === null || ageMinutes === undefined) {
            return i18n("Planify is closed. Open Planify to sync.");
        }
        return i18n("Planify is closed. Tasks last updated %1 ago. Open Planify to sync.", formatAge(ageMinutes));
    }

    function armMidnightTimer() {
        const now = new Date();
        const next = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 0, 0, 5);
        midnightTimer.interval = Math.max(1000, next.getTime() - now.getTime());
        midnightTimer.restart();
    }

    function openPlanify() {
        // Detached, so removing the data source cannot affect the running app.
        launcher.connectSource("setsid -f flatpak run io.github.alainm23.planify >/dev/null 2>&1 </dev/null");
        if (inPanel) {
            expanded = false;
        }
    }

    onReaderArgsChanged: {
        if (busy) {
            configDirty = true;
        }
        refresh();
    }

    Component.onCompleted: {
        armMidnightTimer();
        refresh();
    }

    Plasma5Support.DataSource {
        id: reader
        engine: "executable"
        connectedSources: []
        onNewData: (sourceName, data) => {
            disconnectSource(sourceName);
            if (sourceName === root.currentSource) {
                root.finishRun(data);
            }
        }
    }

    Plasma5Support.DataSource {
        id: launcher
        engine: "executable"
        connectedSources: []
        onNewData: (sourceName, data) => disconnectSource(sourceName)
    }

    Timer {
        interval: 60000
        running: true
        repeat: true
        onTriggered: {
            // Catches a date change that happened during suspend.
            const today = root.currentDayKey();
            if (today !== root.dayKey) {
                root.dayKey = today;
                root.armMidnightTimer();
            }
            root.refresh();
        }
    }

    Timer {
        id: midnightTimer
        repeat: false
        onTriggered: {
            root.dayKey = root.currentDayKey();
            root.armMidnightTimer();
            root.refresh();
        }
    }

    // Backstop in case the engine never reports a result.
    Timer {
        id: watchdog
        interval: 30000
        repeat: false
        onTriggered: {
            if (root.currentSource.length > 0) {
                reader.disconnectSource(root.currentSource);
            }
            root.finishRun({ stdout: "" });
        }
    }

    compactRepresentation: MouseArea {
        id: compact

        readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical
        readonly property bool showBadge: root.hasData && badgeCount > 0
        readonly property int badgeCount: root.counts.today > 0 ? root.counts.today : root.counts.overdue
        property bool wasExpanded: false

        Layout.minimumWidth: vertical ? Kirigami.Units.iconSizes.small : height
        Layout.minimumHeight: vertical ? width : Kirigami.Units.iconSizes.small
        Layout.preferredWidth: Layout.minimumWidth
        Layout.preferredHeight: Layout.minimumHeight

        hoverEnabled: true
        activeFocusOnTab: true
        onPressed: wasExpanded = root.expanded
        onClicked: root.expanded = !wasExpanded
        Keys.onPressed: event => {
            if ([Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter, Qt.Key_Select].includes(event.key)) {
                root.expanded = !root.expanded;
                event.accepted = true;
            }
        }

        Accessible.role: Accessible.Button
        Accessible.name: i18n("Planify Tasks")
        Accessible.description: root.toolTipSubText
        Accessible.onPressAction: root.expanded = !root.expanded

        Kirigami.Icon {
            anchors.fill: parent
            source: Plasmoid.icon
            active: compact.containsMouse || compact.activeFocus
        }

        Rectangle {
            visible: compact.showBadge
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            implicitHeight: badgeLabel.implicitHeight
            implicitWidth: Math.max(implicitHeight, badgeLabel.implicitWidth + Kirigami.Units.smallSpacing)
            radius: height / 2
            color: root.counts.overdue > 0 ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.highlightColor

            PlasmaComponents.Label {
                id: badgeLabel
                anchors.centerIn: parent
                text: compact.badgeCount > 99 ? i18nc("Badge for more than 99 tasks", "99+") : String(compact.badgeCount)
                textFormat: Text.PlainText
                color: Kirigami.Theme.highlightedTextColor
                font: Kirigami.Theme.smallFont
            }
        }
    }

    fullRepresentation: ColumnLayout {
        id: full

        Layout.minimumWidth: Kirigami.Units.gridUnit * 16
        Layout.minimumHeight: Kirigami.Units.gridUnit * 12
        Layout.preferredWidth: Kirigami.Units.gridUnit * 22
        Layout.preferredHeight: Kirigami.Units.gridUnit * (root.inPanel ? 22 : 28)
        spacing: Kirigami.Units.smallSpacing

        RowLayout {
            Layout.fillWidth: true
            spacing: Kirigami.Units.smallSpacing

            Kirigami.Heading {
                Layout.fillWidth: true
                level: 3
                text: i18n("Planify Tasks")
                textFormat: Text.PlainText
                elide: Text.ElideRight
            }

            PlasmaComponents.ToolButton {
                icon.name: "view-task"
                text: i18n("Open Planify")
                Accessible.name: i18n("Open Planify")
                onClicked: root.openPlanify()
            }
        }

        PlasmaComponents.Label {
            Layout.fillWidth: true
            visible: root.staleText.length > 0
            text: root.staleText
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: Kirigami.Theme.neutralTextColor
            font: Kirigami.Theme.smallFont
        }

        // Small notice when older data is still on screen.
        PlasmaComponents.Label {
            Layout.fillWidth: true
            visible: root.hasData && root.lastError.length > 0
            text: i18n("Could not refresh. Showing tasks from %1.",
                       root.lastSuccess.toLocaleTimeString(Qt.locale(), Locale.ShortFormat))
            textFormat: Text.PlainText
            elide: Text.ElideRight
            opacity: 0.7
            font: Kirigami.Theme.smallFont
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            PlasmaComponents.ScrollView {
                anchors.fill: parent
                visible: root.hasData && root.hasVisibleTasks
                PlasmaComponents.ScrollBar.horizontal.policy: PlasmaComponents.ScrollBar.AlwaysOff

                ListView {
                    id: taskList
                    clip: true
                    model: root.rows
                    spacing: 0

                    delegate: Loader {
                        id: rowLoader

                        required property var modelData

                        width: ListView.view ? ListView.view.width : 0
                        sourceComponent: modelData.kind === "header" ? headerComponent : taskComponent

                        Component {
                            id: headerComponent

                            RowLayout {
                                spacing: Kirigami.Units.smallSpacing

                                Kirigami.Heading {
                                    Layout.fillWidth: true
                                    Layout.topMargin: Kirigami.Units.smallSpacing
                                    level: 4
                                    text: rowLoader.modelData.title
                                    textFormat: Text.PlainText
                                    elide: Text.ElideRight
                                    color: rowLoader.modelData.group === "overdue"
                                        ? Kirigami.Theme.negativeTextColor
                                        : Kirigami.Theme.textColor
                                    Accessible.name: i18n("%1: %2 tasks", rowLoader.modelData.title, rowLoader.modelData.count)
                                }

                                PlasmaComponents.Label {
                                    Layout.topMargin: Kirigami.Units.smallSpacing
                                    text: String(rowLoader.modelData.count)
                                    textFormat: Text.PlainText
                                    opacity: 0.7
                                }
                            }
                        }

                        Component {
                            id: taskComponent

                            TaskRow {
                                task: rowLoader.modelData.task
                                onClicked: root.openPlanify()
                            }
                        }
                    }
                }
            }

            ColumnLayout {
                anchors.centerIn: parent
                width: parent.width - Kirigami.Units.gridUnit * 2
                visible: !root.hasData || !root.hasVisibleTasks
                spacing: Kirigami.Units.smallSpacing

                Kirigami.Icon {
                    Layout.alignment: Qt.AlignHCenter
                    implicitWidth: Kirigami.Units.iconSizes.large
                    implicitHeight: Kirigami.Units.iconSizes.large
                    source: !root.hasData && root.lastError.length > 0 ? "dialog-warning" : "view-task"
                    opacity: 0.6
                }

                Kirigami.Heading {
                    Layout.fillWidth: true
                    level: 3
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                    text: {
                        if (root.hasData) {
                            return root.inPanel ? i18n("Nothing due today") : i18n("Nothing due");
                        }
                        return root.lastError.length > 0 ? i18n("Could not read Planify tasks") : i18n("Loading tasks…");
                    }
                }

                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    visible: text.length > 0
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                    opacity: 0.7
                    text: root.hasData ? i18n("You are all caught up.") : root.lastError
                }
            }
        }
    }
}
