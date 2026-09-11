import SwiftUI
import CoreMotion

/// Displays the current atmospheric pressure using the device barometer
/// (via `CMAltimeter`), which is the platform-supported API for reading
/// pressure data on iOS/iPadOS.
struct BarometerToolView: View {
    @State private var state: LoadState = .loading
    @StateObject private var monitor = BarometerMonitor()

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "gauge.with.dots.needle.50percent")
                .font(.system(size: 44))
                .foregroundStyle(.secondary)
                .accessibilityHidden(true)

            content

            if case .value = state {
                Text("Updates automatically while this screen is open")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding()
        .accessibilityElement(children: .contain)
        .onAppear { monitor.start { state = $0 } }
        .onDisappear { monitor.stop() }
    }

    @ViewBuilder
    private var content: some View {
        switch state {
        case .loading:
            ProgressView("Reading pressure…")
                .accessibilityLabel("Loading pressure reading")
        case .unavailable:
            statusView(
                symbol: "exclamationmark.triangle",
                title: "Barometer Unavailable",
                message: "This device does not have a barometer, so pressure data can't be shown."
            )
        case .unauthorized:
            statusView(
                symbol: "lock.shield",
                title: "Motion Access Needed",
                message: "Enable Motion & Fitness access for this app in Settings to read pressure data."
            )
        case .error(let message):
            statusView(symbol: "exclamationmark.circle", title: "Unable to Read Pressure", message: message)
        case .value(let kilopascals):
            VStack(spacing: 4) {
                Text(Self.formattedPressure(kilopascals))
                    .font(.system(size: 48, weight: .light, design: .rounded))
                    .accessibilityLabel("Current pressure \(Self.formattedPressure(kilopascals))")
                Text("kPa")
                    .font(.headline)
                    .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private func statusView(symbol: String, title: String, message: String) -> some View {
        VStack(spacing: 8) {
            Image(systemName: symbol)
                .font(.title)
                .foregroundStyle(.orange)
            Text(title)
                .font(.headline)
            Text(message)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .accessibilityElement(children: .combine)
    }

    private static func formattedPressure(_ kilopascals: Double) -> String {
        String(format: "%.2f", kilopascals)
    }
}

/// Loading, unavailable, permission, and value states for the barometer view.
private enum LoadState: Equatable {
    case loading
    case unavailable
    case unauthorized
    case error(String)
    case value(kilopascals: Double)
}

/// Wraps `CMAltimeter` so `BarometerToolView` can observe pressure updates
/// without owning CoreMotion lifecycle details directly.
@MainActor
private final class BarometerMonitor: ObservableObject {
    private let altimeter = CMAltimeter()

    func start(onUpdate: @escaping (LoadState) -> Void) {
        guard CMAltimeter.isRelativeAltitudeAvailable() else {
            onUpdate(.unavailable)
            return
        }

        switch CMAltimeter.authorizationStatus() {
        case .denied, .restricted:
            onUpdate(.unauthorized)
            return
        case .notDetermined, .authorized:
            break
        @unknown default:
            break
        }

        altimeter.startRelativeAltitudeUpdates(to: .main) { data, error in
            if let error {
                onUpdate(.error(error.localizedDescription))
                return
            }
            guard let data else {
                onUpdate(.error("No pressure data was returned."))
                return
            }
            // `pressure` is reported in kPa.
            onUpdate(.value(kilopascals: data.pressure.doubleValue))
        }
    }

    func stop() {
        altimeter.stopRelativeAltitudeUpdates()
    }
}

#Preview {
    BarometerToolView()
}
