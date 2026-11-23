import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents

// Task text is untrusted: every label below must keep textFormat: Text.PlainText.
PlasmaComponents.ItemDelegate {
    id: row

    property var task: ({})

    readonly property string titleText: textOf(task.title) || i18n("(Untitled task)")
    readonly property string projectText: textOf(task.project)
    readonly property string dueText: textOf(task.due_label)
    readonly property bool isP1: task.priority === 4
    readonly property bool isRecurring: task.is_recurring === true
    readonly property bool isOverdue: task.group === "overdue"

    function textOf(value) {
        return typeof value === "string" ? value : "";
    }

    // Accepts #rgb, #rrggbb and #aarrggbb. The dot is always drawn opaque.
    function dotColor(value) {
        const fallback = Kirigami.Theme.disabledTextColor;
        if (typeof value !== "string") {
            return fallback;
        }
        const match = value.trim().match(/^#(?:[0-9a-f]{2})?([0-9a-f]{6})$|^#([0-9a-f]{3})$/i);
        if (!match) {
            return fallback;
        }
        const hex = match[1] || match[2].replace(/./g, "$&$&");
        return Qt.rgba(parseInt(hex.slice(0, 2), 16) / 255,
                       parseInt(hex.slice(2, 4), 16) / 255,
                       parseInt(hex.slice(4, 6), 16) / 255,
                       1);
    }

    Accessible.name: titleText
    Accessible.description: [
        projectText,
        dueText,
        isP1 ? i18n("Priority 1") : "",
        isRecurring ? i18n("Recurring") : "",
        i18n("Opens Planify"),
    ].filter(part => part.length > 0).join(", ")

    contentItem: RowLayout {
        spacing: Kirigami.Units.smallSpacing

        Rectangle {
            Layout.alignment: Qt.AlignVCenter
            implicitWidth: Kirigami.Units.smallSpacing * 2
            implicitHeight: implicitWidth
            radius: width / 2
            color: row.dotColor(row.task.project_color)
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            spacing: 0

            RowLayout {
                Layout.fillWidth: true
                spacing: Kirigami.Units.smallSpacing

                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: row.titleText
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    maximumLineCount: 1
                }

                PlasmaComponents.Label {
                    visible: row.isP1
                    text: i18nc("Short marker for priority 1", "P1")
                    textFormat: Text.PlainText
                    color: Kirigami.Theme.negativeTextColor
                    font: Kirigami.Theme.smallFont
                }

                Kirigami.Icon {
                    visible: row.isRecurring
                    source: "media-playlist-repeat"
                    implicitWidth: Kirigami.Units.iconSizes.small
                    implicitHeight: Kirigami.Units.iconSizes.small
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Kirigami.Units.smallSpacing

                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: row.projectText
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    maximumLineCount: 1
                    opacity: 0.7
                    font: Kirigami.Theme.smallFont
                }

                PlasmaComponents.Label {
                    visible: row.dueText.length > 0
                    text: row.dueText
                    textFormat: Text.PlainText
                    color: row.isOverdue ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.textColor
                    font: Kirigami.Theme.smallFont
                }
            }
        }
    }
}
