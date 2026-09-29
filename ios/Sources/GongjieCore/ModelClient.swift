import Foundation
import Security

public struct ModelRequest: Sendable {
    public var system: String
    public var messages: [J]
    public var maxTokens: Int
    public var temperature: Double
    public var thinking: Bool
    public init(system: String, messages: [J], maxTokens: Int = 16384, temperature: Double = 0.95, thinking: Bool = false) { self.system = system; self.messages = messages; self.maxTokens = maxTokens; self.temperature = temperature; self.thinking = thinking }
}
public struct ProviderFailure: LocalizedError, Sendable {
    public let code: String
    public let message: String
    public init(_ code: String, _ message: String) { self.code = code; self.message = message }
    public var errorDescription: String? { message }
}

public struct ModelReply: Sendable, Equatable {
    public var text = ""
    public var reasoning = ""
    public var completed = false
    public var finishReason = ""
    public var usage: J = [:]
    public init() {}
    public func validateComplete() throws {
        guard completed, !["length", "max_tokens"].contains(finishReason), !text.trimmed.isEmpty else {
            throw ProviderFailure("response_incomplete", "模型未完整返回结果，请重新记录或再试一次")
        }
        guard finishReason.isEmpty || ["stop", "end_turn", "stop_sequence"].contains(finishReason) else {
            throw ProviderFailure("response_incomplete", "模型提前结束了回应，请再试一次")
        }
    }
}
public protocol LanguageModel: Sendable {
    func generate(_ request: ModelRequest, profile: ProviderProfile, key: String, update: @escaping @Sendable (ModelReply) async -> Void) async throws -> ModelReply
}
public enum KeyVault {
    private static let service = "app.gongjie.model"
    public static func read() throws -> String {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: "primary", kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { throw GameError.keyMissing }
        guard status == errSecSuccess, let data = result as? Data, let value = String(data: data, encoding: .utf8), !value.isEmpty else { throw GameError.invalid("暂时无法读取模型密钥，请解锁设备后重试") }
        return value
    }
    public static func save(_ key: String) throws {
        guard !key.trimmed.isEmpty else { throw GameError.keyMissing }
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: "primary"]
        let attributes: [String: Any] = [kSecValueData as String: Data(key.trimmed.utf8), kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        var status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound { status = SecItemAdd(query.merging(attributes) { _, n in n } as CFDictionary, nil) }
        guard status == errSecSuccess else { throw GameError.invalid("模型密钥没有保存成功") }
    }
    public static func delete() throws {
        let status = SecItemDelete([kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: "primary"] as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw GameError.invalid("密钥没有移除成功") }
    }
}

/// Event framing is separate from provider decoding, so Unicode and cancellation stay transport concerns.
public struct SSEDecoder: Sendable {
    private var dataLines: [String] = []
    public init() {}
    public mutating func line(_ line: String) -> String? {
        if line.isEmpty { return flush() }
        if line.hasPrefix("data:") { var data = String(line.dropFirst(5)); if data.hasPrefix(" ") { data.removeFirst() }; dataLines.append(data) }
        return nil
    }
    public mutating func flush() -> String? { guard !dataLines.isEmpty else { return nil }; let data = dataLines.joined(separator: "\n"); dataLines = []; return data }
}
public struct SSEByteDecoder: Sendable {
    private var buffer = Data()
    private var framing = SSEDecoder()
    public init() {}
    public mutating func append(_ byte: UInt8) throws -> String? {
        if byte == 10 { return try processLine() }
        guard buffer.count < 4_000_000 else { throw GameError.invalid("模型事件过大") }
        buffer.append(byte); return nil
    }
    private mutating func processLine() throws -> String? {
        if buffer.last == 13 { buffer.removeLast() }
        guard let line = String(data: buffer, encoding: .utf8) else { throw GameError.invalid("模型返回了无效的文字编码") }
        buffer.removeAll(keepingCapacity: true)
        return framing.line(line)
    }
    public mutating func finish() throws -> String? {
        if !buffer.isEmpty { _ = try processLine() }
        return framing.flush()
    }
}
public enum WireAdapter {
    public static func request(_ input: ModelRequest, profile: ProviderProfile, key: String) throws -> URLRequest {
        try profile.validate()
        let endpoint = profile.baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/" + profile.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let url = URL(string: endpoint), !key.trimmed.isEmpty else { throw GameError.keyMissing }
        var request = URLRequest(url: url); request.httpMethod = "POST"; request.timeoutInterval = 180
        request.setValue("application/json", forHTTPHeaderField: "Content-Type"); request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        var body: J = ["model": .string(profile.model), "stream": true, "max_tokens": .number(Double(input.maxTokens)), "temperature": .number(input.temperature)]
        if profile.wireProtocol == "anthropic-messages" {
            request.setValue(key, forHTTPHeaderField: "x-api-key"); request.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
            body["system"] = .string(input.system); body["messages"] = .array(input.messages)
        } else {
            request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
            body["messages"] = .array([["role": "system", "content": .string(input.system)]] + input.messages)
            body["stream_options"] = ["include_usage": true]
            if profile.model.split(separator: "/").last?.hasPrefix("deepseek-v4-") == true {
                body["thinking"] = ["type": .string(input.thinking ? "enabled" : "disabled")]
                if input.thinking { body["reasoning_effort"] = "low" }
            }
        }
        request.httpBody = try JSONEncoder().encode(body); return request
    }
    public static func consume(_ payload: String, protocol name: String, reply: inout ModelReply) throws {
        if payload == "[DONE]" { reply.completed = true; return }
        guard let data = payload.data(using: .utf8), let event = try? JSONDecoder().decode(J.self, from: data) else { throw ProviderFailure("protocol", "模型返回了无法读取的数据") }
        if event["error"] != .null || event["type"].string == "error" { throw ProviderFailure("provider", "模型服务返回错误，请检查连接与额度") }
        if name == "anthropic-messages" {
            switch event["type"].string {
            case "message_start": reply.usage = reply.usage.merging(event["message"]["usage"])
            case "content_block_delta": reply.text += event["delta"]["text"].string; reply.reasoning += event["delta"]["thinking"].string
            case "message_delta":
                reply.usage = reply.usage.merging(event["usage"])
                if !event["delta"]["stop_reason"].string.isEmpty { reply.completed = true; reply.finishReason = event["delta"]["stop_reason"].string }
            case "message_stop": reply.completed = true
            default: break
            }
        } else {
            if let choice = event["choices"].array.first {
                reply.text += choice["delta"]["content"].string
                reply.reasoning += choice["delta"]["reasoning_content"].string
                if !choice["finish_reason"].string.isEmpty { reply.completed = true; reply.finishReason = choice["finish_reason"].string }
            }
            reply.usage = reply.usage.merging(event["usage"])
        }
    }
}
private final class NoRedirects: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}
public struct ModelClient: LanguageModel {
    public init() {}
    public func generate(_ input: ModelRequest, profile: ProviderProfile, key: String, update: @escaping @Sendable (ModelReply) async -> Void) async throws -> ModelReply {
        let request = try WireAdapter.request(input, profile: profile, key: key)
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 180; config.timeoutIntervalForResource = 600
        let session = URLSession(configuration: config, delegate: NoRedirects(), delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        do {
            let (bytes, response) = try await session.bytes(for: request)
            guard let response = response as? HTTPURLResponse else { throw ProviderFailure("network", "模型连接没有返回有效响应") }
            guard (200..<300).contains(response.statusCode) else {
                let hint: String
                switch response.statusCode { case 401, 403: hint = "模型密钥无效或无权访问"; case 429: hint = "请求过于频繁或额度不足，请稍后重试"; case 404: hint = "没有找到模型或接口，请检查设置"; default: hint = "模型请求失败（\(response.statusCode)）" }
                throw ProviderFailure("http_\(response.statusCode)", hint)
            }
            var decoder = SSEByteDecoder(), reply = ModelReply()
            for try await byte in bytes {
                try Task.checkCancellation()
                if let payload = try decoder.append(byte) { try WireAdapter.consume(payload, protocol: profile.wireProtocol, reply: &reply); await update(reply) }
            }
            if let payload = try decoder.finish() { try WireAdapter.consume(payload, protocol: profile.wireProtocol, reply: &reply); await update(reply) }
            try Task.checkCancellation()
            try reply.validateComplete()
            return reply
        } catch let error as URLError {
            if error.code == .cancelled || Task.isCancelled { throw CancellationError() }
            throw ProviderFailure(error.code == .timedOut ? "timeout" : "network", error.code == .timedOut ? "模型响应超时，请重试" : "无法连接模型，请检查网络")
        }
    }
}
