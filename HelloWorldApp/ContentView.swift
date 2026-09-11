import SwiftUI

private enum ToolOption: String, CaseIterable {
    case calendar
    case calculator
    case barometer

    var title: String {
        switch self {
        case .calendar:
            return "Calendar"
        case .calculator:
            return "Calculator"
        case .barometer:
            return "Barometer"
        }
    }

    var symbolName: String {
        switch self {
        case .calendar:
            return "calendar"
        case .calculator:
            return "calculator"
        case .barometer:
            return "speedometer"
        }
    }
}

struct ContentView: View {
    @State private var message = "Hello there Aaron!"

    var body: some View {
        NavigationStack {
            TextEditor(text: $message)
                .padding()
                .navigationTitle("Hello World")
                .toolbar {
                    ToolbarItem(placement: .primaryAction) {
                        Menu {
                            Menu("Tools") {
                                ForEach(ToolOption.allCases, id: \.self) { tool in
                                    Button(action: {}) {
                                        Label(tool.title, systemImage: tool.symbolName)
                                    }
                                    .accessibilityLabel(tool.title)
                                }
                            }
                        } label: {
                            Label("Menu", systemImage: "line.3.horizontal")
                                .accessibilityLabel("Open app menu")
                        }
                    }
                }
        }
    }
}
