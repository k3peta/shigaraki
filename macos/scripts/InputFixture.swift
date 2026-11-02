import AppKit

/// Disposable native editor with real Command-V handling; contains no user document.
@MainActor final class InputFixtureDelegate: NSObject, NSApplicationDelegate {
    private var window: NSWindow!
    func applicationDidFinishLaunching(_ notification: Notification) {
        let bar = NSMenu()
        let appItem = NSMenuItem(); let appMenu = NSMenu()
        appMenu.addItem(withTitle: "入力テストを終了", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu; bar.addItem(appItem)
        let editItem = NSMenuItem(); let editMenu = NSMenu(title: "編集")
        editMenu.addItem(withTitle: "コピー", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "貼り付け", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "すべてを選択", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu; bar.addItem(editItem); NSApp.mainMenu = bar
        NSApp.setActivationPolicy(.regular)
        window = NSWindow(contentRect: NSRect(x: 80, y: 200, width: 650, height: 350),
                          styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        window.title = "信楽 入力テスト（保存しない文書）"; window.isReleasedWhenClosed = false
        let scroll = NSScrollView(frame: window.contentView!.bounds)
        scroll.hasVerticalScroller = true; scroll.autoresizingMask = [.width, .height]
        let text = NSTextView(frame: scroll.bounds)
        if ProcessInfo.processInfo.arguments.contains("--group-role") { text.setAccessibilityRole(.group) }
        if ProcessInfo.processInfo.arguments.contains("--non-text-role") { text.setAccessibilityRole(.button) }
        if ProcessInfo.processInfo.arguments.contains("--secure-subrole") { text.setAccessibilitySubrole(.secureTextField) }
        text.isRichText = false; text.font = .systemFont(ofSize: 22)
        text.textContainerInset = NSSize(width: 18, height: 18)
        text.string = "前🙂\n\n後"; text.setSelectedRange(NSRange(location: 3, length: 0))
        text.setAccessibilityIdentifier("koeflow-fixture")
        scroll.documentView = text; window.contentView = scroll
        window.makeKeyAndOrderFront(nil); window.makeFirstResponder(text); NSApp.activate(ignoringOtherApps: true)
    }
}

@main struct InputFixture {
    @MainActor static func main() {
        let app = NSApplication.shared; let delegate = InputFixtureDelegate(); app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
