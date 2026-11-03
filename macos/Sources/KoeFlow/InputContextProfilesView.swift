import SwiftUI
import KoeFlowCore

struct InputContextProfilesView: View {
    @ObservedObject var settings: Settings
    var applications: [(id: String, name: String)]
    var lastBundleID: String
    @State private var kind: InputContextProfile.Kind = .application
    @State private var identifier = ""
    @State private var selectedApp = ""
    @State private var expandedID: UUID?
    init(settings: Settings, applications: [(id: String, name: String)], lastBundleID: String, initialIdentifier: String = "") {
        self.settings = settings; self.applications = applications; self.lastBundleID = lastBundleID
        self._identifier = State(initialValue: initialIdentifier)
    }
    private var normalized: String? { InputContextProfile.normalizedIdentifier(identifier, kind: kind) }
    private var existing: InputContextProfile? {
        guard let normalized else { return nil }
        return settings.inputContextProfiles.prefix(InputContextProfile.maximumProfiles).first { $0.kind == kind && $0.identifier == normalized }
    }
    var body: some View {
        GroupBox("アプリ・サイト別の文脈（任意）") {
            VStack(alignment: .leading, spacing: 10) {
                Text("話題や用語を補足すると、同じ入力先での認識の参考にします。登録しなくても使えます。用語を本文へ機械的に置き換える設定ではありません。")
                    .font(.system(size: 13)).foregroundStyle(.secondary)
                Picker("対象", selection: $kind) {
                    Text("アプリ").tag(InputContextProfile.Kind.application)
                    Text("Webサイト").tag(InputContextProfile.Kind.website)
                }.pickerStyle(.segmented)
                    .onChange(of: kind) { _, _ in identifier = ""; selectedApp = "" }
                if kind == .application {
                    Picker("起動中のアプリ", selection: $selectedApp) {
                        Text("アプリを選ぶ").tag("")
                        ForEach(applications, id: \.id) { Text($0.name).tag($0.id) }
                    }.onChange(of: selectedApp) { _, value in if !value.isEmpty { identifier = value } }
                }
                HStack {
                    TextField(kind == .application ? "アプリの識別子（例：com.example.app）" : "https://example.com または example.com", text: $identifier)
                        .textFieldStyle(.roundedBorder)
                    Button(existing == nil ? "追加" : "編集") {
                        guard let normalized else { return }
                        if let existing { expandedID = existing.id }
                        else {
                            guard settings.inputContextProfiles.count < InputContextProfile.maximumProfiles else { return }
                            let profile = InputContextProfile(kind: kind, identifier: normalized)
                            settings.inputContextProfiles.append(profile); expandedID = profile.id
                        }
                        identifier = normalized
                    }.disabled(!InputContextProfile.canAddOrEdit(settings.inputContextProfiles, kind: kind, identifier: identifier))
                }
                if settings.inputContextProfiles.count >= InputContextProfile.maximumProfiles {
                    Text("文脈設定は64件までです。登録済みの設定は編集できます。")
                        .font(.system(size: 13)).foregroundStyle(.secondary)
                }
                if kind == .application && !lastBundleID.isEmpty {
                    Button("直前の入力先を使う") { identifier = lastBundleID }
                        .controlSize(.small)
                }
                Text("Webサイトはhttp・httpsのホスト名だけを保存し、完全に一致する場合に使います。ページのパスや検索語は保存しません。入力先を確認できない場合や、使う情報が「アプリ名のみ」の場合は適用しません。")
                    .font(.system(size: 13)).foregroundStyle(.secondary)
                ForEach($settings.inputContextProfiles) { $profile in
                    DisclosureGroup(isExpanded: Binding(get: { expandedID == profile.id }, set: { expandedID = $0 ? profile.id : nil })) {
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Toggle("有効", isOn: $profile.enabled)
                                Spacer()
                                Button("削除") { settings.inputContextProfiles.removeAll { $0.id == profile.id } }
                                    .controlSize(.small)
                            }
                            Text("話題の補足（400字まで）").font(.system(size: 13, weight: .medium))
                            TextField("この入力先でよく話す内容", text: Binding(get: { profile.topic }, set: { profile.topic = String($0.prefix(400)) }), axis: .vertical)
                                .lineLimit(2...4).textFieldStyle(.roundedBorder)
                            Text("優先する用語（1行1語、32語まで・各80字まで）").font(.system(size: 13, weight: .medium))
                            TextEditor(text: Binding(get: { profile.terms.joined(separator: "\n") }, set: {
                                profile.terms = Array($0.components(separatedBy: .newlines).prefix(32)).map { String($0.prefix(80)) }
                            })).font(.system(size: 14)).frame(height: 80)
                                .overlay(RoundedRectangle(cornerRadius: 4).stroke(Color.secondary.opacity(0.25)))
                        }.padding(.vertical, 6)
                    } label: {
                        Text((profile.kind == .website ? "Web · " : "アプリ · ") + profile.identifier + (profile.enabled ? "" : "（オフ）"))
                            .font(.system(size: 14, weight: .medium))
                    }
                }
            }.padding(6)
        }
    }
}
