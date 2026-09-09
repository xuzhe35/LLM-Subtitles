import Foundation
import Vision
import ImageIO

struct Line: Codable {
    let text: String
    let confidence: Float
    let box: [String: Double]
}
let args = CommandLine.arguments
if args.count < 2 { exit(2) }
do {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    if args.count > 2 && args[2] != "auto" { request.recognitionLanguages = [args[2]] }
    let handler = VNImageRequestHandler(url: URL(fileURLWithPath: args[1]), options: [:])
    try handler.perform([request])
    let lines: [Line] = (request.results ?? []).compactMap { observation in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let b = observation.boundingBox
        return Line(text: candidate.string, confidence: candidate.confidence,
                    box: ["x": max(0, b.minX), "y": max(0, 1-b.maxY), "width": min(1-b.minX, b.width), "height": min(b.maxY, b.height)])
    }
    let encoder = JSONEncoder()
    print(String(data: try encoder.encode(lines), encoding: .utf8)!)
} catch {
    fputs("Local Vision recognition failed: \(error.localizedDescription)\n", stderr)
    exit(1)
}
