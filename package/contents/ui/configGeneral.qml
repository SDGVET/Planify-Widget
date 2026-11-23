import QtQuick
import QtQuick.Controls as QQC2
import org.kde.kcmutils as KCMUtils
import org.kde.kirigami as Kirigami

KCMUtils.SimpleKCM {
    id: page

    // Plain properties, not aliases: each SpinBox binds to these after its from/to are set.
    property int cfg_soonDays: 3
    property int cfg_minPriority: 4
    property int cfg_staleMinutes: 60

    // The Plasma config dialog also passes each entry's default value.
    property int cfg_soonDaysDefault: 3
    property int cfg_minPriorityDefault: 4
    property int cfg_staleMinutesDefault: 60

    readonly property var priorityChoices: [
        { text: i18n("P1 only"), value: 4 },
        { text: i18n("P2 and higher"), value: 3 },
        { text: i18n("P3 and higher"), value: 2 },
    ]

    function priorityIndex(value) {
        const index = priorityChoices.findIndex(choice => choice.value === value);
        return index >= 0 ? index : 0;
    }

    Kirigami.FormLayout {
        QQC2.SpinBox {
            id: soonDaysSpin
            Kirigami.FormData.label: i18n("Due soon window (days):")
            Accessible.name: i18n("Due soon window in days")
            from: 1
            to: 30
            value: page.cfg_soonDays
            editable: true
            onValueModified: page.cfg_soonDays = value
        }

        QQC2.ComboBox {
            Kirigami.FormData.label: i18n("Important priority:")
            Accessible.name: i18n("Lowest priority shown as important")
            model: page.priorityChoices
            textRole: "text"
            valueRole: "value"
            currentIndex: page.priorityIndex(page.cfg_minPriority)
            onActivated: index => page.cfg_minPriority = page.priorityChoices[index].value
        }

        QQC2.SpinBox {
            id: staleMinutesSpin
            Kirigami.FormData.label: i18n("Warn when closed and unchanged for (minutes):")
            Accessible.name: i18n("Minutes after which to warn when Planify is closed and its data is unchanged")
            from: 5
            to: 1440
            stepSize: 5
            value: page.cfg_staleMinutes
            editable: true
            onValueModified: page.cfg_staleMinutes = value
        }
    }
}
