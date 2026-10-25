// Small flat button for the chat panel: kind = normal | primary | danger | success.
import QtQuick 2.10

Rectangle {
    id: button
    property string text: ""
    property string hoverText: ""     // shown instead of text while hovered (if set)
    property string kind: "normal"
    property bool dangerOnHover: false
    property int fontSize: 12
    signal clicked()

    readonly property bool hovered: mouse.containsMouse
    readonly property var palette: ({
        normal:  { bg: "#ffffff", hover: "#eef4f9", fg: "#1f2427", border: "#c9d1d7" },
        primary: { bg: "#0277bd", hover: "#01649f", fg: "#ffffff", border: "#0277bd" },
        danger:  { bg: "#d32f2f", hover: "#b71c1c", fg: "#ffffff", border: "#d32f2f" },
        success: { bg: "#2e7d32", hover: "#1b5e20", fg: "#ffffff", border: "#2e7d32" }
    })
    readonly property var colors: (dangerOnHover && hovered) ? palette.danger : (palette[kind] || palette.normal)

    implicitWidth: label.implicitWidth + 18
    implicitHeight: label.implicitHeight + 10
    radius: 4
    opacity: enabled ? 1.0 : 0.45
    color: hovered && enabled ? colors.hover : colors.bg
    border.color: colors.border
    border.width: 1

    Text {
        id: label
        anchors.centerIn: parent
        text: (button.hovered && button.hoverText) ? button.hoverText : button.text
        color: button.colors.fg
        font.pixelSize: button.fontSize
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: button.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: if (button.enabled) button.clicked()
    }
}
