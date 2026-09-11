import SwiftUI
import UIKit

/// Displays a calendar for the current month, highlighting today's date.
struct CalendarToolView: View {
    private let calendar = Calendar.current

    @State private var referenceDate = Date()

    var body: some View {
        VStack(spacing: 16) {
            Text(monthTitle)
                .font(.title2.bold())
                .accessibilityAddTraits(.isHeader)

            LazyVGrid(columns: Array(repeating: GridItem(.flexible()), count: 7), spacing: 8) {
                ForEach(weekdaySymbols, id: \.self) { symbol in
                    Text(symbol)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .accessibilityHidden(true)
                }

                ForEach(0..<leadingEmptyDays, id: \.self) { index in
                    Color.clear
                        .frame(height: 36)
                        .accessibilityHidden(true)
                        .id("leading-\(index)")
                }

                ForEach(daysInMonth, id: \.self) { day in
                    dayCell(for: day)
                }
            }

            Spacer(minLength: 0)
        }
        .padding()
        .accessibilityElement(children: .contain)
        .onAppear { referenceDate = Date() }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.significantTimeChangeNotification)) { _ in
            referenceDate = Date()
        }
    }

    @ViewBuilder
    private func dayCell(for day: Date) -> some View {
        let dayNumber = calendar.component(.day, from: day)
        let isToday = calendar.isDateInToday(day)

        Button(action: {}) {
            Text("\(dayNumber)")
                .frame(maxWidth: .infinity, minHeight: 36)
                .background(isToday ? Color.accentColor : Color.clear)
                .foregroundStyle(isToday ? Color.white : Color.primary)
                .clipShape(Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(accessibilityLabel(for: day, isToday: isToday))
        .accessibilityAddTraits(isToday ? [.isSelected] : [])
    }

    private func accessibilityLabel(for day: Date, isToday: Bool) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "EEEE, MMMM d, yyyy"
        let dateString = formatter.string(from: day)
        return isToday ? "\(dateString), today" : dateString
    }

    /// Start of the currently displayed month.
    private var monthStart: Date {
        calendar.dateInterval(of: .month, for: referenceDate)?.start ?? referenceDate
    }

    /// Every date in the currently displayed month, in order.
    /// `Calendar.range(of:in:for:)` returns the correct day count for the
    /// month (28-31 days), automatically accounting for leap years.
    private var daysInMonth: [Date] {
        guard let range = calendar.range(of: .day, in: .month, for: referenceDate) else { return [] }
        return range.compactMap { day in
            calendar.date(byAdding: .day, value: day - 1, to: monthStart)
        }
    }

    /// Number of blank leading cells needed so the 1st of the month lines
    /// up under the correct weekday column.
    private var leadingEmptyDays: Int {
        let firstWeekday = calendar.component(.weekday, from: monthStart)
        return (firstWeekday - calendar.firstWeekday + 7) % 7
    }

    private var monthTitle: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "LLLL yyyy"
        return formatter.string(from: referenceDate)
    }

    /// Weekday abbreviations ordered starting from the calendar's first weekday.
    private var weekdaySymbols: [String] {
        let symbols = calendar.shortWeekdaySymbols
        let firstIndex = calendar.firstWeekday - 1
        return Array(symbols[firstIndex...] + symbols[..<firstIndex])
    }
}

#Preview {
    CalendarToolView()
}
