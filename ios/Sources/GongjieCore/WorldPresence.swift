import Foundation

public extension StoryWorld {
    func activeMembers(threadID: String) -> [String] {
        guard let thread = threads.first(where: { $0.id == threadID }), thread.isRoom else { return [] }
        return thread.characterIDs.filter { id in
            characterRooms[id] == threadID && character(id)["current_location_id"].string == thread.locationID
                && actions[id]?["availability"].string != "unavailable"
        }
    }
    mutating func gatherRoom(threadID: String) throws {
        guard let thread = threads.first(where: { $0.id == threadID }), thread.isRoom,
              let location = thread.locationID, locations.contains(where: { $0["id"].string == location }),
              !thread.characterIDs.isEmpty else { throw GameError.invalid("请选择有效的同场与地点") }
        for id in thread.characterIDs {
            guard let index = characters.firstIndex(where: { $0["id"].string == id }) else { throw GameError.missing }
            characterRooms[id] = threadID; characters[index]["current_location_id"] = .string(location)
            actions[id] = (actions[id] ?? [:]).merging(["availability": "present"])
            witnessLocation(characterID: id, locationID: location, sourceID: threadID)
        }
        witnessLocation(characterID: "player", locationID: location, sourceID: threadID)
        player["location_id"] = .string(location); playerThreadID = threadID
    }
    mutating func enterThread(threadID: String) throws {
        guard let thread = threads.first(where: { $0.id == threadID }) else { throw GameError.missing }
        if thread.isRoom {
            guard let location = thread.locationID, locations.contains(where: { $0["id"].string == location }) else { throw GameError.missing }
            player["location_id"] = .string(location)
        } else {
            guard let id = thread.characterIDs.first, !character(id).object.isEmpty,
                  availability(id) != "unavailable" else { throw GameError.invalid("此刻暂时无法接触这个人") }
            characterRooms.removeValue(forKey: id)
            player["location_id"] = character(id)["current_location_id"]
            witnessLocation(characterID: id, locationID: character(id)["current_location_id"].string, sourceID: threadID)
        }
        witnessLocation(characterID: "player", locationID: player["location_id"].string, sourceID: threadID)
        playerThreadID = threadID
    }
    func assertCurrent(threadID: String) throws {
        guard playerThreadID == threadID, let thread = threads.first(where: { $0.id == threadID }) else { throw GameError.invalid("现场已经变化，请重新进入") }
        if thread.isRoom {
            guard player["location_id"].string == thread.locationID, !activeMembers(threadID: threadID).isEmpty else { throw GameError.invalid("这个现场的人已经离开") }
        } else {
            guard let id = thread.characterIDs.first, character(id)["current_location_id"] == player["location_id"], availability(id) != "unavailable" else { throw GameError.invalid("现场已经变化，请重新进入角色") }
        }
    }
    mutating func clearDepartedPresence() {
        characterRooms = characterRooms.filter { id, roomID in
            guard let room = threads.first(where: { $0.id == roomID && $0.isRoom }), room.characterIDs.contains(id) else { return false }
            return character(id)["current_location_id"].string == room.locationID && actions[id]?["availability"].string != "unavailable"
        }
        if let id = playerThreadID, let thread = threads.first(where: { $0.id == id }) {
            let location = thread.isRoom ? thread.locationID ?? "" : character(thread.characterIDs.first ?? "")["current_location_id"].string
            if location != player["location_id"].string { playerThreadID = nil }
        }
    }
}
