import SwiftUI

struct ContentView: View {
    @State private var message = "Hello there Aaron!"

    var body: some View {
        NavigationStack {
            TextEditor(text: $message)
                .padding()
                .navigationTitle("Hello World")
        }
    }
}
