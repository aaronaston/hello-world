import SwiftUI
import UIKit

/// Basic four-function calculator supporting addition, subtraction,
/// multiplication, and division, with clear/reset behavior and visible
/// handling of invalid operations such as division by zero.
struct CalculatorToolView: View {
    private enum Operation: String {
        case add = "+"
        case subtract = "−"
        case multiply = "×"
        case divide = "÷"

        func apply(_ lhs: Double, _ rhs: Double) -> Double? {
            switch self {
            case .add:
                return lhs + rhs
            case .subtract:
                return lhs - rhs
            case .multiply:
                return lhs * rhs
            case .divide:
                guard rhs != 0 else { return nil }
                return lhs / rhs
            }
        }
    }

    private static let buttonRows: [[String]] = [
        ["C", "±", "%", "÷"],
        ["7", "8", "9", "×"],
        ["4", "5", "6", "−"],
        ["1", "2", "3", "+"],
        ["0", ".", "="],
    ]

    @State private var displayValue = "0"
    @State private var pendingValue: Double?
    @State private var pendingOperation: Operation?
    @State private var isEnteringNumber = false
    @State private var errorMessage: String?

    var body: some View {
        VStack(spacing: 12) {
            Text(errorMessage ?? displayValue)
                .font(.system(size: 48, weight: .light, design: .rounded))
                .foregroundStyle(errorMessage == nil ? .primary : .red)
                .lineLimit(1)
                .minimumScaleFactor(0.4)
                .frame(maxWidth: .infinity, alignment: .trailing)
                .padding()
                .accessibilityLabel(errorMessage.map { "Error: \($0)" } ?? "Result: \(displayValue)")
                .accessibilityIdentifier("calculatorDisplay")

            ForEach(Self.buttonRows, id: \.self) { row in
                HStack(spacing: 8) {
                    ForEach(row, id: \.self) { label in
                        calculatorButton(label)
                    }
                }
            }
        }
        .padding()
        .accessibilityElement(children: .contain)
        .background(KeyboardInputCatcher(onKey: handleKeyInput))
    }

    @ViewBuilder
    private func calculatorButton(_ label: String) -> some View {
        Button(action: { handleInput(label) }) {
            Text(label)
                .font(.title2.weight(.medium))
                .frame(maxWidth: .infinity, minHeight: 56)
                .background(backgroundColor(for: label))
                .foregroundStyle(foregroundColor(for: label))
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(accessibilityLabel(for: label))
    }

    private func backgroundColor(for label: String) -> Color {
        switch label {
        case "C", "±", "%":
            return Color(.systemGray4)
        case "÷", "×", "−", "+", "=":
            return Color.orange
        default:
            return Color(.systemGray5)
        }
    }

    private func foregroundColor(for label: String) -> Color {
        switch label {
        case "C", "±", "%", "÷", "×", "−", "+", "=":
            return .white
        default:
            return .primary
        }
    }

    private func accessibilityLabel(for label: String) -> String {
        switch label {
        case "C": return "Clear"
        case "±": return "Toggle sign"
        case "%": return "Percent"
        case "÷": return "Divide"
        case "×": return "Multiply"
        case "−": return "Subtract"
        case "+": return "Add"
        case "=": return "Equals"
        case ".": return "Decimal point"
        default: return label
        }
    }

    // MARK: - Input handling

    private func handleInput(_ label: String) {
        switch label {
        case "C":
            reset()
        case "±":
            toggleSign()
        case "%":
            applyPercent()
        case "0", "1", "2", "3", "4", "5", "6", "7", "8", "9":
            appendDigit(label)
        case ".":
            appendDecimalPoint()
        case "+", "−", "×", "÷":
            selectOperation(Operation(rawValue: label))
        case "=":
            evaluate()
        default:
            break
        }
    }

    /// Maps physical keyboard characters onto the same logic as on-screen buttons.
    private func handleKeyInput(_ character: String) {
        switch character {
        case "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", ".":
            handleInput(character)
        case "+":
            handleInput("+")
        case "-":
            handleInput("−")
        case "*":
            handleInput("×")
        case "/":
            handleInput("÷")
        case "=", "\r", "\n":
            handleInput("=")
        case "\u{7F}", "\u{8}":
            reset()
        default:
            break
        }
    }

    private func reset() {
        displayValue = "0"
        pendingValue = nil
        pendingOperation = nil
        isEnteringNumber = false
        errorMessage = nil
    }

    private func toggleSign() {
        guard let value = Double(displayValue) else { return }
        displayValue = Self.format(-value)
    }

    private func applyPercent() {
        guard let value = Double(displayValue) else { return }
        displayValue = Self.format(value / 100)
    }

    private func appendDigit(_ digit: String) {
        errorMessage = nil
        if isEnteringNumber && displayValue != "0" {
            displayValue += digit
        } else {
            displayValue = digit == "0" && displayValue == "0" ? "0" : digit
        }
        isEnteringNumber = true
    }

    private func appendDecimalPoint() {
        errorMessage = nil
        if !isEnteringNumber {
            displayValue = "0."
            isEnteringNumber = true
        } else if !displayValue.contains(".") {
            displayValue += "."
        }
    }

    private func selectOperation(_ operation: Operation?) {
        guard let operation else { return }
        errorMessage = nil
        if let pendingOperation, let pendingValue, isEnteringNumber {
            guard let currentValue = Double(displayValue) else { return }
            if let result = pendingOperation.apply(pendingValue, currentValue) {
                displayValue = Self.format(result)
                self.pendingValue = result
            } else {
                showError()
                return
            }
        } else {
            pendingValue = Double(displayValue)
        }
        self.pendingOperation = operation
        isEnteringNumber = false
    }

    private func evaluate() {
        guard let pendingOperation, let pendingValue, let currentValue = Double(displayValue) else { return }
        if let result = pendingOperation.apply(pendingValue, currentValue) {
            displayValue = Self.format(result)
            self.pendingValue = nil
            self.pendingOperation = nil
            isEnteringNumber = false
            errorMessage = nil
        } else {
            showError()
        }
    }

    private func showError() {
        errorMessage = "Cannot divide by zero"
        pendingValue = nil
        pendingOperation = nil
        isEnteringNumber = false
        displayValue = "0"
    }

    private static func format(_ value: Double) -> String {
        if value.truncatingRemainder(dividingBy: 1) == 0, abs(value) < 1e15 {
            return String(format: "%.0f", value)
        }
        return String(value)
    }
}

/// Invisible view that captures hardware keyboard input on iOS/iPadOS so the
/// calculator can be operated without touching the on-screen buttons.
private struct KeyboardInputCatcher: UIViewRepresentable {
    let onKey: (String) -> Void

    func makeUIView(context: Context) -> KeyInputView {
        let view = KeyInputView()
        view.onKey = onKey
        return view
    }

    func updateUIView(_ uiView: KeyInputView, context: Context) {
        uiView.onKey = onKey
    }

    final class KeyInputView: UIView {
        var onKey: ((String) -> Void)?

        override var canBecomeFirstResponder: Bool { true }

        override func didMoveToWindow() {
            super.didMoveToWindow()
            becomeFirstResponder()
        }

        override var keyCommands: [UIKeyCommand]? {
            let characters = "0123456789.+-*/=\r"
            return characters.map { UIKeyCommand(input: String($0), modifierFlags: [], action: #selector(handleKeyCommand(_:))) }
        }

        @objc private func handleKeyCommand(_ command: UIKeyCommand) {
            guard let input = command.input else { return }
            onKey?(input)
        }
    }
}

#Preview {
    CalculatorToolView()
}
