import SwiftUI
import KoeFlowCore
import ServiceManagement

private let flowBlue = Color(red: 0.25, green: 0.69, blue: 0.93)

struct FlowView: View {
    @ObservedObject var model: FlowModel
    @ObservedObject var settings: Settings
    @State private var tab = 0
    @State private var heard = ""
    @State private var written = ""
    @State private var placeQuery = ""
    @State private var wordBundle = ""
    @State private var matchKana = true
    @State private var wordMode: WordRuleMode = .automatic
    @State private var profileBundle = ""
    @State private var profileApps: [(id: String, name: String)] = []
    @State private var editingCardID = "standard.email"
    private var initialContextIdentifier: String
    init(model: FlowModel, settings: Settings, initialTab: Int = 0, initialContextIdentifier: String = "") {
        self.model = model; self.settings = settings; self._tab = State(initialValue: initialTab)
        self.initialContextIdentifier = initialContextIdentifier
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .center) {
                Image(systemName: "waveform.circle.fill").font(.system(size: 38)).foregroundStyle(flowBlue.opacity(0.85))
                VStack(alignment: .leading, spacing: 3) {
                    HStack(alignment: .firstTextBaseline, spacing: 9) {
                        Text("信楽").font(.system(size: 26, weight: .semibold))
                        Text("しがらき").font(.system(size: 13)).foregroundStyle(.secondary)
                    }
                    Text("Haas · Mac内で日本語を認識").font(.system(size: 13)).foregroundStyle(.secondary)
                }
                Spacer()
                Label(settings.recognitionLogEnabled ? "端末内・ログ保存" : "端末内認識", systemImage: "lock.shield").font(.system(size: 13, weight: .medium))
                    .padding(.horizontal, 10).padding(.vertical, 7).background(flowBlue.opacity(0.1), in: Capsule())
            }
            Picker("表示", selection: $tab) {
                Text("音声入力").tag(0); Text("設定").tag(2); Text("単語辞書").tag(1); Text("アプリ・サイト").tag(3); Text("カード").tag(4)
            }.pickerStyle(.segmented).labelsHidden()
            if tab == 0 { dictation }
            else if tab == 1 { dictionary }
            else if tab == 2 { ScrollView { preferences }.scrollIndicators(.automatic) }
            else if tab == 3 { ScrollView { applicationProfiles }.scrollIndicators(.automatic) }
            else { ScrollView { promptCardEditor }.scrollIndicators(.automatic) }
            Spacer(minLength: 0)
            HStack {
                Button("最後の入力をコピー", systemImage: "doc.on.doc") { model.copyLastInput() }
                    .buttonStyle(.bordered).controlSize(.regular)
                    .disabled(model.isBusy || model.lastInput.isEmpty)
                    .help("転記できなかった文章も、認識できたところまでコピーします。")
                Text("入力パネル：" + settings.shortcutDescription + "\nメインパネル：1回で録音／停止\n2回で閉じて転記")
                    .font(.system(size: 13)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                Spacer()
                Text("v" + (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "—")).font(.system(size: 13)).foregroundStyle(.tertiary)
            }
        }
        .padding(.horizontal, 24).padding(.top, 24).padding(.bottom, 20)
        .frame(width: 600, height: model.editingWorkspace ? 740 : 680)
        .background(Color(nsColor: .windowBackgroundColor))
        .font(.system(size: 14))
        .onAppear { model.refreshPermission(); settings.refreshLoginStatus(); refreshProfileApps() }
        .onChange(of: tab) { _, value in if value == 1 || value == 3 { refreshProfileApps() } }
        .onChange(of: model.continuous) { _, active in if active { tab = 0 } }
        .onChange(of: model.showDictationRequest) { _, _ in tab = 0 }
    }
    private var dictation: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Circle().fill(model.phase == .listening ? Color.red : Color.secondary.opacity(0.4)).frame(width: 7, height: 7)
                Text(phaseLabel).font(.system(size: 14, weight: .medium))
                Spacer()
                Text(model.targetName).font(.system(size: 13)).foregroundStyle(.secondary)
            }
            HStack(spacing: 12) {
                WaveMeter(level: model.level, active: model.phase == .listening).frame(width: 86, height: 24)
                Text(String(format: "%02d:%02d", Int(model.elapsed) / 60, Int(model.elapsed) % 60))
                    .font(.system(size: 14, design: .monospaced)).foregroundStyle(.secondary)
                if let latency = model.firstTextLatency {
                    Text(String(format: "最初の文字 %.1f秒", latency)).font(.system(size: 13)).foregroundStyle(.secondary)
                }
                Spacer()
                Button {
                    if model.isBusy { model.toggle() } else if model.editingWorkspace { model.start(voiceEdit: true) } else { model.toggleContinuous() }
                } label: {
                    Image(systemName: model.isBusy ? "stop.circle.fill" : "mic.circle")
                        .font(.system(size: 38)).frame(width: 44, height: 44)
                        .foregroundStyle(model.phase == .listening ? Color.red : Color.primary)
                        .contentShape(Circle())
                }
                    .buttonStyle(.plain)
                    .opacity(model.phase == .finishing || model.phase == .polishing ? 0.5 : 1)
                    .accessibilityLabel(model.phase == .listening ? "停止して文章を保持" : model.phase == .preparing ? "準備を中止" : model.editingWorkspace ? "音声で編集" : "録音を開始")
                    .help(model.phase == .listening ? "停止して文章を保持" : model.phase == .preparing ? "準備を中止" : model.editingWorkspace ? "音声で編集" : "録音を開始")
                    .disabled(model.phase == .finishing || model.phase == .polishing)
            }
            HStack {
                Picker("文章の用途", selection: $settings.writingPurpose) {
                    ForEach(WritingPurpose.allCases, id: \.self) { Text($0.title).tag($0) }
                }.frame(maxWidth: 250).disabled(model.isBusy)
                Spacer()
                Text("今回：" + model.writingPurpose.title).font(.system(size: 13)).foregroundStyle(.secondary)
            }
            Picker("共通の加工カード", selection: $settings.promptCardID) {
                Text("用途に合う標準カード").tag("")
                ForEach(settings.promptCards) { Text($0.name).tag($0.id) }
            }.disabled(model.isBusy)
            ZStack(alignment: .topLeading) {
                RoundedRectangle(cornerRadius: 16).fill(Color.primary.opacity(0.035))
                if model.editingWorkspace || (!model.isBusy && !model.text.isEmpty) {
                    DraftEditor(text: $model.text, selection: $model.draftSelection, editable: !model.isBusy,
                                rules: model.reviewWordRules, onBeginEditing: { if model.requiresReplacementReview { model.beginTextReview() } })
                        .clipShape(RoundedRectangle(cornerRadius: 16))
                } else if model.text.isEmpty {
                    VStack(alignment: .leading, spacing: 9) {
                        Text("ここに、話した内容が入力されます。").font(.system(size: 19)).foregroundStyle(.secondary)
                        Text("キーを1回：録音／停止\nキーを2回：閉じて転記。停止後の文章は保持します。")
                            .font(.system(size: 14)).foregroundStyle(.secondary).lineSpacing(5)
                    }.padding(18)
                } else {
                    ScrollViewReader { proxy in ScrollView {
                        Text(model.text).font(.system(size: 21)).lineSpacing(7).textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading).padding(18)
                        Color.clear.frame(height: 1).id("transcriptEnd")
                    }.onChange(of: model.text) { _, _ in
                        if model.phase == .listening { proxy.scrollTo("transcriptEnd", anchor: .bottom) }
                    } }
                }
            }.frame(height: 200)
            if !model.isBusy && model.reviewWordRules.contains(where: { $0.mode == .suggestion }) && !model.text.isEmpty {
                Text("下線の語をクリックして候補を選べます。選択後に⌥↓でも開けます。置換は⌘Zで戻せます。")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
            }
            if model.editingWorkspace && model.voiceEditing && !model.rawText.isEmpty {
                Text("音声指示：" + model.rawText).font(.system(size: 13)).foregroundStyle(.secondary).lineLimit(2)
            }
            Text(model.status).font(.system(size: 14)).foregroundStyle(model.inputFailed ? Color.orange : Color.secondary).lineSpacing(3)
                .frame(maxWidth: .infinity, alignment: .leading).frame(minHeight: 26)
            if !model.logError.isEmpty { Text(model.logError).font(.system(size: 13)).foregroundStyle(.orange) }
            HStack {
                Button("本文をコピー", systemImage: "doc.on.doc") { model.copy() }.disabled(model.text.isEmpty)
                Button(model.editingWorkspace ? "確認して貼り付け" : "閉じて転記") {
                    if model.editingWorkspace { model.retryLastInput?() }
                    else { model.requestWindowCommit(force: true) }
                }
                    .disabled(model.isBusy || model.text.isEmpty)
                    .help("貼り付け先を選んで⌃⌥Vでも再転記できます。ボタンは直前のアプリの入力欄へ貼り付けます。")
                Spacer()
            }.buttonStyle(.glass).controlSize(.regular)
            HStack {
                Button("用途に合わせて整える", systemImage: "sparkles") { model.polishLocally() }
                    .disabled(model.isBusy || model.text.isEmpty || !model.localPolishAvailable)
                    .help("Apple Intelligenceで用途に合わせて整えます。このボタンの結果は自動転記しません。")
                Button("原文へ戻す") { model.restoreOriginal() }.disabled(model.isBusy || !model.canRestoreOriginal)
            }.buttonStyle(.glass).controlSize(.regular)
            HStack {
                if model.phase == .polishing { Button("整形を中止") { model.toggle() } }
                if model.editingWorkspace { Button("通常入力に戻る") { model.endEditingWorkspace() }.disabled(model.isBusy) }
            }.buttonStyle(.glass).controlSize(.regular)
        }
    }
    private var dictionary: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("名前などの表記を固定する場合は「自動置換」、文脈で使い分ける語は「選択候補」で登録します。選択候補は自動で置換しません。")
                .font(.system(size: 14)).foregroundStyle(.secondary)
            Picker("登録の扱い", selection: $wordMode) {
                ForEach(WordRuleMode.allCases, id: \.self) { Text($0.title).tag($0) }
            }.pickerStyle(.segmented).disabled(model.isBusy)
            HStack {
                Picker("適用先", selection: $wordBundle) {
                    Text("全アプリ共通").tag("")
                    ForEach(profileApps, id: \.id) { Text($0.name).tag($0.id) }
                    if !wordBundle.isEmpty && !profileApps.contains(where: { $0.id == wordBundle }) {
                        Text("選択済みのアプリ").tag(wordBundle)
                    }
                }.frame(maxWidth: 240)
                Toggle("かなの表記差も一致させる", isOn: $matchKana).font(.system(size: 13))
            }.disabled(model.isBusy)
            HStack {
                TextField("認識される表記・読み", text: $heard)
                Image(systemName: "arrow.right").foregroundStyle(.secondary)
                TextField("入力する表記", text: $written)
                Button("追加") {
                    settings.addWordRule(WordRule(heard: heard.trimmingCharacters(in: .whitespacesAndNewlines),
                        written: written.trimmingCharacters(in: .whitespacesAndNewlines), bundleID: wordBundle.isEmpty ? nil : wordBundle, matchKana: matchKana, mode: wordMode))
                    heard = ""; written = ""
                }.disabled(heard.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || written.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || model.isBusy)
            }
            VStack(alignment: .leading, spacing: 6) {
                TextField("地名を検索（漢字・かな／例：千葉 そうさ）", text: $placeQuery)
                    .accessibilityLabel("地名を検索")
                if let places = LocalGeography.dictionary {
                    if !placeQuery.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        let candidates = places.search(placeQuery, limit: 3)
                        if candidates.isEmpty { Text("該当する地名はありません。").font(.system(size: 13)).foregroundStyle(.secondary) }
                        ForEach(candidates) { place in
                            HStack {
                                Text(place.name).font(.system(size: 14, weight: .medium))
                                Text(place.reading + " · " + place.prefecture).font(.system(size: 13)).foregroundStyle(.secondary)
                                Spacer()
                                Button("選択") { heard = place.reading; written = place.name }
                                    .controlSize(.small).disabled(model.isBusy)
                                    .accessibilityLabel(place.name + "を選択")
                            }
                        }
                    }
                    Text("全国の都道府県・市区町村を端末内で検索します。選択後、読みや誤認識された表記を確認して「追加」を押してください。地名は自動では登録されません。")
                        .font(.system(size: 12)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                } else { Text("地名データを読み込めませんでした。手動で単語を登録できます。").font(.system(size: 13)).foregroundStyle(.orange) }
            }.padding(10).background(flowBlue.opacity(0.06), in: RoundedRectangle(cornerRadius: 12))
            List {
                if settings.rules.isEmpty { Text("まだ単語が登録されていません。").foregroundStyle(.secondary) }
                ForEach(settings.rules) { rule in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(rule.heard)
                            Text(wordScopeName(rule) + (rule.matchKana ? " · かな対応" : ""))
                                .font(.system(size: 12)).foregroundStyle(.secondary)
                        }.frame(maxWidth: .infinity, alignment: .leading)
                        Image(systemName: "arrow.right").foregroundStyle(.secondary)
                        Text(rule.written).frame(maxWidth: .infinity, alignment: .leading)
                        Picker("登録の扱い", selection: Binding(get: {
                            settings.rules.first { $0.id == rule.id }?.mode ?? rule.mode
                        }, set: { mode in
                            if let index = settings.rules.firstIndex(where: { $0.id == rule.id }) { settings.rules[index].mode = mode }
                        })) {
                            ForEach(WordRuleMode.allCases, id: \.self) { Text($0.title).tag($0) }
                        }.labelsHidden().frame(width: 95).disabled(model.isBusy)
                        Button { settings.rules.removeAll { $0.id == rule.id } } label: { Image(systemName: "minus.circle") }
                            .buttonStyle(.plain).disabled(model.isBusy)
                    }.padding(.vertical, 4)
                }
            }.listStyle(.plain).scrollContentBackground(.hidden)
                .background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 16))
        }
    }
    private func wordScopeName(_ rule: WordRule) -> String {
        guard let id = rule.bundleID else { return "全アプリ共通" }
        return profileApps.first { $0.id == id }?.name ??
            NSWorkspace.shared.urlForApplication(withBundleIdentifier: id)?.deletingPathExtension().lastPathComponent ?? "指定したアプリ"
    }
    private var preferences: some View {
        VStack(alignment: .leading, spacing: 12) {
            GroupBox("アプリ") {
                VStack(alignment: .leading, spacing: 10) {
                    Toggle("ログイン時に起動する", isOn: Binding(get: { settings.launchAtLogin }, set: { settings.setLaunchAtLogin($0) }))
                        .toggleStyle(.checkbox).disabled(model.isBusy)
                    HStack {
                        Text(settings.loginMessage).font(.system(size: 13)).foregroundStyle(.secondary)
                        Spacer()
                        if settings.loginStatus == .requiresApproval || settings.loginError != nil {
                            Button("ログイン項目を開く") { SMAppService.openSystemSettingsLoginItems() }.controlSize(.small)
                        }
                    }
                    Button("信楽を終了", systemImage: "power") { model.quitApplication?() }
                }.padding(8).frame(maxWidth: .infinity, alignment: .leading)
            }
            GroupBox {
                VStack(alignment: .leading, spacing: 12) {
                    Picker("操作に使うキー", selection: $settings.holdModifier) {
                        Text("左Control（標準）").tag("leftControl")
                        Text("左Option").tag("leftOption")
                        Text("このキーを使わない").tag("none")
                    }
                    Text("メインパネルでは1回で録音／停止、2回で閉じて転記します。入力パネルでは、押している間だけ録音します。録音は1回5分までです。")
                        .font(.system(size: 13)).foregroundStyle(.secondary)
                    Picker("連続入力の開始・停止", selection: $settings.continuousShortcut) {
                        ForEach(ContinuousShortcut.allCases, id: \.self) { Text($0.title).tag($0) }
                    }
                    Picker("メインパネルを呼び出すキー", selection: $settings.windowShortcut) {
                        ForEach(WindowShortcut.allCases, id: \.self) { Text($0.title).tag($0) }
                    }
                    Text("閉じたメインパネルを開く2回押しのキーを選べます。別のキーを選んだ場合も、メインパネル内ではそのキーの2回押しで確定・転記できます。")
                        .font(.system(size: 13)).foregroundStyle(.secondary)
                    Text("修飾キーだけの組み合わせは、両方押して離すと切り替わります。押し続ける必要はありません。")
                        .font(.system(size: 13)).foregroundStyle(.secondary)
                    Text("認識する言語：日本語（Haas）")
                    Toggle("入力パネルで停止したら入力欄へ転記", isOn: $settings.liveInput)
                    Toggle("⌃⌥Spaceを押している間だけ話す", isOn: $settings.holdToTalk)
                    Toggle("「改行して」「段落を変えて」を改行に置き換える", isOn: $settings.commands)
                    Toggle("認識した文字をテキストログに保存", isOn: $settings.recognitionLogEnabled)
                }.padding(8).frame(maxWidth: .infinity, alignment: .leading)
            }.disabled(model.isBusy)
            HStack {
                Text("録音は保存しません。文字ログには日時・入力先・転記結果を残し、テキストとJSONLで保存します。整理・アーカイブは手動で行います。")
                    .font(.system(size: 13)).foregroundStyle(.secondary)
                Button("ログを開く") { model.openRecognitionLog() }.controlSize(.small)
                Button("保存先") { model.openLogFolder() }.controlSize(.small)
            }
            HStack {
                Label(model.accessibility ? "直接入力の許可あり" : "直接入力の許可が必要", systemImage: model.accessibility ? "checkmark.circle" : "hand.raised")
                Spacer()
                Button("許可を確認") { model.requestAccessibility() }
                Button(SetupCopy.guide) { model.openSetup?() }
            }.font(.system(size: 14))
            Text("音声はMac内で認識し、録音は保存しません。文字ログは設定に従って保存します。初回の音声モデルの準備には通信が必要です。入力先やカーソルが変わると、直接入力を停止して字幕表示を継続します。")
                .font(.system(size: 14)).foregroundStyle(.secondary).lineSpacing(4)
            Divider()
            Text("文章の整形").font(.system(size: 15, weight: .semibold))
            Toggle("停止後に用途に合わせて整形する", isOn: $settings.automaticWriting)
                .disabled(model.isBusy)
            Text("自動整形は最大4秒を目安に待ちます。整形できない場合は原文を転記します。手動の整形結果は、確認してコピーしてください。")
                .font(.system(size: 13)).foregroundStyle(.secondary).lineSpacing(3)
            Picker("共通の用途", selection: $settings.writingPurpose) {
                ForEach(WritingPurpose.allCases, id: \.self) { Text($0.title).tag($0) }
            }.disabled(model.isBusy)
            Picker("整形に使う情報", selection: $settings.contextScope) {
                ForEach(ContextScope.allCases, id: \.self) { Text($0.title).tag($0) }
            }.disabled(model.isBusy)
            Text(model.localPolishAvailable ? "Apple Intelligenceによる端末内整形を利用できます。" : "端末内整形にはApple Intelligenceを有効にする必要があります。")
                .font(.system(size: 14)).foregroundStyle(.secondary)
            Toggle("直近に見たページのタイトルも使う", isOn: $settings.pageContextEnabled)
                .disabled(model.isBusy)
            Text("初期設定ではアプリ名、前面ウィンドウのタイトル、入力欄の前後の文章を使います。信楽の起動中に前面で見たWebページのタイトルも、最大3件・3分間だけメモリに保持して参考にします。ページ本文やブラウザの閲覧履歴は取得しません。設定をオフにするとページの記憶を消します。以前に保存した取得範囲とアプリ別設定は引き継ぎます。")
                .font(.system(size: 14)).foregroundStyle(.secondary).lineSpacing(4)
            Divider()
            HStack {
                Button("操作マニュアル", systemImage: "book") { model.openManual?() }
                Button("操作早見表", systemImage: "list.bullet.rectangle") { model.openQuickGuide?() }
            }.buttonStyle(.glass).controlSize(.regular)
        }
    }
    private func refreshProfileApps() {
        var seen = Set<String>()
        profileApps = NSWorkspace.shared.runningApplications.compactMap { app in
            guard app.activationPolicy == .regular, let id = app.bundleIdentifier, id != Bundle.main.bundleIdentifier,
                  seen.insert(id).inserted else { return nil }
            return (id, app.localizedName ?? id)
        }.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }
    private var applicationProfiles: some View {
        VStack(alignment: .leading, spacing: 14) {
            InputContextProfilesView(settings: settings, applications: profileApps, lastBundleID: model.lastTargetBundle, initialIdentifier: initialContextIdentifier)
            Divider()
            Text("文章の整形").font(.system(size: 15, weight: .semibold))
            Text("アプリごとに用途・整形方法・使う情報を指定できます。指定がないアプリには共通設定を使います。")
                .font(.system(size: 14)).foregroundStyle(.secondary)
            HStack {
                Picker("起動中のアプリ", selection: $profileBundle) {
                    Text("アプリを選ぶ").tag("")
                    ForEach(profileApps, id: \.id) { Text($0.name).tag($0.id) }
                }
                Button("更新") { refreshProfileApps() }
                Button("追加") {
                    if let app = profileApps.first(where: { $0.id == profileBundle }) {
                        settings.appProfiles.append(AppWritingProfile(bundleID: app.id, name: app.name, scope: settings.contextScope)); profileBundle = ""
                    }
                }.disabled(profileBundle.isEmpty || settings.appProfiles.contains { $0.bundleID == profileBundle })
            }
            if !model.lastTargetBundle.isEmpty {
                Button("直前の入力先「" + model.lastTargetApp + "」を追加") { model.addLastTargetProfile() }
                    .disabled(settings.appProfiles.contains { $0.bundleID == model.lastTargetBundle })
            }
            if settings.appProfiles.isEmpty { Text("まだアプリ別の設定がありません。").foregroundStyle(.secondary) }
            ForEach($settings.appProfiles) { $profile in
                GroupBox {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack {
                            Text(profile.name).font(.system(size: 15, weight: .semibold))
                            Spacer()
                            Button("削除") { settings.appProfiles.removeAll { $0.id == profile.id } }.controlSize(.small)
                        }
                        Picker("用途", selection: $profile.purpose) {
                            ForEach(WritingPurpose.allCases, id: \.self) { Text($0.title).tag($0) }
                        }
                        Picker("整形方法", selection: $profile.mode) {
                            ForEach(WritingMode.allCases, id: \.self) { Text($0.title).tag($0) }
                        }
                        Picker("加工カード", selection: Binding(get: { profile.promptCardID ?? "inherit" }, set: { profile.promptCardID = $0 == "inherit" ? nil : $0 })) {
                            Text("共通のカード設定").tag("inherit")
                            Text("用途に合う標準カード").tag("")
                            ForEach(settings.promptCards) { Text($0.name).tag($0.id) }
                        }
                        Picker("使う情報", selection: $profile.scope) {
                            ForEach(ContextScope.allCases, id: \.self) { Text($0.title).tag($0) }
                        }
                    }.padding(6)
                }
            }
            Text("ブラウザは用途を自動判定にし、ウィンドウ情報を使う設定にすると、メールとチャットを切り替えられます。用途が不明な場合や、原文を選んだ場合は書き換えません。")
                .font(.system(size: 13)).foregroundStyle(.secondary).lineSpacing(3)
        }.disabled(model.isBusy)
    }
    private var phaseLabel: String {
        if model.voiceEditing && model.isBusy { return model.phase == .polishing ? "音声編集：変更中" : "音声編集：指示を受付中" }
        switch model.phase { case .idle: return "待機中"; case .preparing: return "準備中"; case .listening: return "聞いています"; case .finishing: return "確定中"; case .polishing: return "整えています" }
    }
    private var promptCardEditor: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("標準カードには、用途、整形の指示、入力例、出力例がそろっています。カスタムカードは指示だけでも使えます。標準カードも編集できます。")
                .font(.system(size: 14)).foregroundStyle(.secondary)
            HStack {
                Picker("編集するカード", selection: $editingCardID) {
                    ForEach(settings.promptCards) { Text($0.name).tag($0.id) }
                }
                Button("新規") {
                    let card = PromptCard(name: "新しいカード", instructions: "読みやすい段落と句読点に整えてください。")
                    settings.promptCards.append(card); editingCardID = card.id
                }
                Button("複製") {
                    guard let source = settings.promptCards.first(where: { $0.id == editingCardID }) else { return }
                    let card = PromptCard(name: source.name + "のコピー", purpose: source.purpose, instructions: source.instructions,
                                          inputExample: source.inputExample, outputExample: source.outputExample)
                    settings.promptCards.append(card); editingCardID = card.id
                }
            }
            ForEach($settings.promptCards) { $card in
                if card.id == editingCardID {
                    TextField("カード名", text: $card.name)
                    Picker("カードの用途", selection: $card.purpose) {
                        Text("メール").tag(WritingPurpose.email); Text("チャット").tag(WritingPurpose.chat); Text("文書").tag(WritingPurpose.document)
                    }.disabled(card.isStandard)
                    Text("整形の指示").font(.system(size: 14, weight: .medium))
                    TextEditor(text: $card.instructions).font(.system(size: 15)).frame(height: 130)
                        .accessibilityLabel("整形の指示")
                        .padding(6).background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 10))
                        .onChange(of: card.instructions) { _, value in if value.count > 1200 { card.instructions = String(value.prefix(1200)) } }
                    HStack {
                        Text("\(card.instructions.count) / 1200文字・変更は自動保存").font(.system(size: 13)).foregroundStyle(.secondary)
                        Spacer()
                        if card.isStandard {
                            Button("標準に戻す") {
                                if let standard = PromptCard.standards.first(where: { $0.id == card.id }) { card = standard }
                            }
                        } else {
                            Button("削除") {
                                let id = card.id
                                settings.promptCards.removeAll { $0.id == id }
                                if settings.promptCardID == id { settings.promptCardID = "" }
                                for index in settings.appProfiles.indices where settings.appProfiles[index].promptCardID == id { settings.appProfiles[index].promptCardID = "" }
                                editingCardID = "standard.email"
                            }
                        }
                    }.controlSize(.small)
                    VStack(alignment: .leading, spacing: 6) {
                        Text("入力例（任意）").font(.system(size: 14, weight: .medium))
                        TextEditor(text: $card.inputExample).font(.system(size: 15)).frame(height: 64)
                            .accessibilityLabel("入力例（任意）")
                            .padding(6).background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 10))
                            .onChange(of: card.inputExample) { _, value in if value.count > 300 { card.inputExample = String(value.prefix(300)) } }
                        Text("出力例（任意）").font(.system(size: 14, weight: .medium))
                        TextEditor(text: $card.outputExample).font(.system(size: 15)).frame(height: 64)
                            .accessibilityLabel("出力例（任意）")
                            .padding(6).background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 10))
                            .onChange(of: card.outputExample) { _, value in if value.count > 300 { card.outputExample = String(value.prefix(300)) } }
                        Text("例文は各300文字まで。入力例と出力例の両方があるときだけ使われます。指示が空の場合は、用途に合う標準の指示を使います。")
                            .font(.system(size: 13)).foregroundStyle(.secondary)
                    }
                }
            }
            Text("「音声入力」や「アプリ別」で使用するカードを選びます。指定したカードの用途を使いますが、「原文」と「整形しない」は優先します。手動整形で試してから、自動整形をオンにしてください。")
                .font(.system(size: 13)).foregroundStyle(.secondary).lineSpacing(3)
        }.disabled(model.isBusy)
    }
}

struct WaveMeter: View {
    var level: Float
    var active: Bool
    private var amplitude: CGFloat { level.isFinite ? CGFloat(min(1, max(0, level))) : 0 }
    var body: some View {
        HStack(alignment: .center, spacing: 3) {
            ForEach(0..<(active ? 12 : 8), id: \.self) { i in
                let envelope = 0.55 + 0.45 * sin(CGFloat(i) / 11 * .pi)
                Capsule().fill(active && amplitude > 0.04 ? flowBlue : Color.secondary.opacity(active ? 0.45 : 0.25))
                    .frame(width: active ? 4 : 3, height: active ? 4 + 18 * amplitude * envelope : 3)
            }
        }.animation(.easeOut(duration: 0.07), value: level)
    }
}

struct OverlayView: View {
    @ObservedObject var model: FlowModel
    @ObservedObject var settings: Settings
    private var compact: Bool { model.phase == .idle && model.text.isEmpty && model.status == "入力欄を選び、長押しで開始" }
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack {
                WaveMeter(level: model.level, active: model.phase == .listening)
                    .frame(width: model.phase == .listening ? 86 : 50, height: 22)
                    .accessibilityLabel(model.phase == .listening ? "聞いています" : "信楽")
                if model.phase != .listening {
                    Text(model.voiceEditing && model.isBusy ? "音声編集" : model.phase == .idle ? "信楽" : model.phase == .polishing ? "整形中" : "準備・確定中")
                        .font(.system(size: 13, weight: .semibold))
                }
                Spacer()
                Button { model.copy() } label: { Image(systemName: "doc.on.doc") }.buttonStyle(.plain).help("字幕をコピー")
                Button { model.openSettings?() } label: { Image(systemName: "gearshape") }.buttonStyle(.plain).help("設定・辞書・全文")
                Button { model.toggle() } label: { Image(systemName: model.isBusy ? "stop.circle.fill" : "mic.circle.fill") }
                    .buttonStyle(.plain).help("開始・停止")
                    .foregroundStyle(model.phase == .listening ? Color.red : Color.primary)
                Button { model.hideOverlay?() } label: { Image(systemName: "xmark") }.buttonStyle(.plain).help("字幕を閉じる")
            }
            Text(model.text.isEmpty ? settings.shortcutDescription + "で話し始める" : String(model.text.suffix(100)))
                .font(.system(size: 16)).lineSpacing(0).frame(maxWidth: .infinity, alignment: .leading)
                .lineLimit(3).truncationMode(.head).frame(height: compact ? 20 : 60, alignment: .topLeading)
            if !compact {
                Text(model.voiceEditing || model.inputFailed ? model.status : model.phase == .preparing || model.text.isEmpty && model.phase == .idle ? model.status : model.direct ? model.targetName + " · " + model.writingPurpose.title : "端末内認識 · " + model.writingPurpose.title)
                    .font(.system(size: 12)).foregroundStyle(model.inputFailed ? Color.orange : Color.secondary)
                    .lineLimit(1).help(model.status)
            }
        }.padding(.horizontal, 14).padding(.vertical, 10).frame(width: 328, height: compact ? 80 : 132)
            .foregroundStyle(.primary)
            .glassEffect(.regular.tint(flowBlue.opacity(0.035)), in: RoundedRectangle(cornerRadius: 22))
    }
}
