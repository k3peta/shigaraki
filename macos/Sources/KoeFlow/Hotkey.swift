import Carbon

@MainActor final class Hotkey {
    var onPress: (() -> Void)?
    var onRelease: (() -> Void)?
    private var hotkey: EventHotKeyRef?
    private var handler: EventHandlerRef?
    private static var nextID: UInt32 = 1
    private let identifier: UInt32
    init() { identifier = Self.nextID; Self.nextID += 1 }
    func unregister() {
        if let hotkey { UnregisterEventHotKey(hotkey); self.hotkey = nil }
        if let handler { RemoveEventHandler(handler); self.handler = nil }
    }
    func register(keyCode: UInt32 = UInt32(kVK_Space), modifiers: UInt32 = UInt32(controlKey | optionKey)) -> Bool {
        unregister()
        var events = [EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed)),
                      EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyReleased))]
        let context = Unmanaged.passUnretained(self).toOpaque()
        let result = InstallEventHandler(GetApplicationEventTarget(), { _, event, context in
            guard let event, let context else { return OSStatus(eventNotHandledErr) }
            let hotkey = Unmanaged<Hotkey>.fromOpaque(context).takeUnretainedValue()
            var pressed = EventHotKeyID()
            guard GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                nil, MemoryLayout<EventHotKeyID>.size, nil, &pressed) == noErr,
                pressed.signature == 0x4b464c57, pressed.id == hotkey.identifier else { return OSStatus(eventNotHandledErr) }
            let kind = GetEventKind(event)
            DispatchQueue.main.async {
                if kind == UInt32(kEventHotKeyPressed) { hotkey.onPress?() }
                if kind == UInt32(kEventHotKeyReleased) { hotkey.onRelease?() }
            }
            return noErr
        }, events.count, &events, context, &handler)
        guard result == noErr else { return false }
        let id = EventHotKeyID(signature: 0x4b464c57, id: identifier)
        return RegisterEventHotKey(keyCode, modifiers, id, GetApplicationEventTarget(), 0, &hotkey) == noErr
    }
    deinit {
        if let hotkey { UnregisterEventHotKey(hotkey) }
        if let handler { RemoveEventHandler(handler) }
    }
    /// Exercises Carbon event dispatch inside this process; does not generate keyboard input.
    static func routingSelfTest() async -> [String: Any] {
        let codes = [UInt32(kVK_F16), UInt32(kVK_F17), UInt32(kVK_F18), UInt32(kVK_F19)]
        let keys = codes.map { _ in Hotkey() }
        var received = codes.map { _ in 0 }
        var released = codes.map { _ in 0 }
        for index in keys.indices {
            keys[index].onPress = { received[index] += 1 }
            keys[index].onRelease = { released[index] += 1 }
            guard keys[index].register(keyCode: codes[index],
                                      modifiers: UInt32(controlKey | optionKey | shiftKey)) else { return ["passed": false, "error": "registration"] }
        }
        for key in keys {
          for kind in [UInt32(kEventHotKeyPressed), UInt32(kEventHotKeyReleased)] {
            var event: EventRef?
            guard CreateEvent(nil, OSType(kEventClassKeyboard), kind,
                              GetCurrentEventTime(), EventAttributes(0), &event) == noErr, let event else { return ["passed": false, "error": "event"] }
            var id = EventHotKeyID(signature: 0x4b464c57, id: key.identifier)
            SetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                              MemoryLayout<EventHotKeyID>.size, &id)
            SendEventToEventTarget(event, GetApplicationEventTarget())
            ReleaseEvent(event)
          }
        }
        try? await Task.sleep(for: .milliseconds(50))
        for key in keys { key.unregister() }
        return ["passed": received == [1, 1, 1, 1] && released == [1, 1, 1, 1], "received": received, "released": released]
    }
}
