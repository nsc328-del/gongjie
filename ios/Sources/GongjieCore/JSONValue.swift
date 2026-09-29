import Foundation

public enum JSONValue: Codable, Sendable, Hashable, ExpressibleByStringLiteral, ExpressibleByIntegerLiteral, ExpressibleByBooleanLiteral, ExpressibleByArrayLiteral, ExpressibleByDictionaryLiteral {
    case object([String: JSONValue]), array([JSONValue]), string(String), number(Double), bool(Bool), null
    public init(stringLiteral value: String) { self = .string(value) }
    public init(integerLiteral value: Int) { self = .number(Double(value)) }
    public init(booleanLiteral value: Bool) { self = .bool(value) }
    public init(arrayLiteral elements: JSONValue...) { self = .array(elements) }
    public init(dictionaryLiteral elements: (String, JSONValue)...) { self = .object(Dictionary(uniqueKeysWithValues: elements)) }
    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let x = try? c.decode(Bool.self) { self = .bool(x) }
        else if let x = try? c.decode(String.self) { self = .string(x) }
        else if let x = try? c.decode(Double.self) { self = .number(x) }
        else if let x = try? c.decode([JSONValue].self) { self = .array(x) }
        else { self = .object(try c.decode([String: JSONValue].self)) }
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self { case .object(let v): try c.encode(v); case .array(let v): try c.encode(v); case .string(let v): try c.encode(v); case .number(let v): try c.encode(v); case .bool(let v): try c.encode(v); case .null: try c.encodeNil() }
    }
    public var object: [String: JSONValue] { if case .object(let v) = self { return v }; return [:] }
    public var array: [JSONValue] { if case .array(let v) = self { return v }; return [] }
    public var string: String { if case .string(let v) = self { return v }; return "" }
    public var int: Int { if case .number(let v) = self, v.isFinite, v >= Double(Int.min), v < Double(Int.max) { return Int(v) }; return 0 }
    public var double: Double { if case .number(let v) = self { return v }; return 0 }
    public var bool: Bool { if case .bool(let v) = self { return v }; return false }
    public var strings: [String] { array.map(\.string) }
    public subscript(_ key: String) -> JSONValue {
        get { object[key] ?? .null }
        set { var d = object; d[key] = newValue; self = .object(d) }
    }
    public func selecting(_ keys: [String]) -> JSONValue { .object(object.filter { keys.contains($0.key) }) }
    public func merging(_ other: JSONValue) -> JSONValue { .object(object.merging(other.object) { _, new in new }) }
    public var text: String { String(data: (try? JSONEncoder().encode(self)) ?? Data(), encoding: .utf8) ?? "{}" }
    public static func parse(_ text: String) throws -> JSONValue {
        var value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if value.hasPrefix("```") { value = value.components(separatedBy: "\n").dropFirst().joined(separator: "\n"); if value.hasSuffix("```") { value.removeLast(3) } }
        guard let data = value.data(using: .utf8), let result = try? JSONDecoder().decode(JSONValue.self, from: data), case .object = result else { throw GameError.invalid("模型没有返回有效的完整 JSON 对象") }
        return result
    }
    public static func strings(_ list: [String]) -> JSONValue { .array(list.map { .string($0) }) }
}
public typealias J = JSONValue
public enum GameError: LocalizedError, Sendable {
    case invalid(String), pending, busy, missing, incomplete, keyMissing
    public var errorDescription: String? {
        switch self { case .invalid(let s): return s; case .pending: return "先把上一段余波重新记录，再继续故事"; case .busy: return "这个世界仍在继续，请稍候"; case .missing: return "这段记录已经不存在"; case .incomplete: return "回应中途断开，未完成的正文尚未记录到世界"; case .keyMissing: return "先连接你的模型" }
    }
}
public func newID() -> String { UUID().uuidString.lowercased() }
public extension String { var trimmed: String { trimmingCharacters(in: .whitespacesAndNewlines) } }
