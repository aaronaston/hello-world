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
    @State private var selectedTool: ToolOption?

    var body: some View {
        NavigationStack {
            Group {
                switch selectedTool {
                case .calendar:
                    CalendarToolView()
                case .calculator:
                    CalculatorToolView()
                case .barometer:
                    BarometerToolView()
                case .none:
                    TextEditor(text: $message)
                        .padding()
                }
            }
            .navigationTitle(navigationTitle)
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Menu {
                        Menu("Tools") {
                            ForEach(ToolOption.allCases, id: \.self) { tool in
                                Button(action: { selectTool(tool) }) {
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

    private var navigationTitle: String {
        selectedTool?.title ?? "Hello World"
    }

    private func selectTool(_ tool: ToolOption) {
        selectedTool = tool
    }
}
