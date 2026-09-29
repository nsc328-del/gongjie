import AppKit
let size = 1024
let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8, samplesPerPixel: 3, hasAlpha: false, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
let rect = NSRect(x: 0, y: 0, width: size, height: size)
NSGradient(starting: NSColor(red: 0.14, green: 0.27, blue: 0.22, alpha: 1), ending: NSColor(red: 0.28, green: 0.44, blue: 0.35, alpha: 1))!.draw(in: rect, angle: 65)
NSColor(red: 0.92, green: 0.90, blue: 0.79, alpha: 1).setStroke()
for x in [260.0, 440.0] {
 let arch = NSBezierPath()
 arch.move(to: NSPoint(x:x, y:270)); arch.line(to:NSPoint(x:x,y:565))
 arch.curve(to:NSPoint(x:x+320,y:565),controlPoint1:NSPoint(x:x,y:805),controlPoint2:NSPoint(x:x+320,y:805))
 arch.line(to:NSPoint(x:x+320,y:270)); arch.lineWidth = 25; arch.lineCapStyle = .round; arch.stroke()
}
let horizon = NSBezierPath(); horizon.move(to:NSPoint(x:224,y:270)); horizon.line(to:NSPoint(x:800,y:270)); horizon.lineWidth = 22; horizon.lineCapStyle = .round; horizon.stroke()
NSColor(red: 0.92, green: 0.90, blue: 0.79, alpha: 1).setFill(); NSBezierPath(ovalIn:NSRect(x:492,y:410,width:40,height:40)).fill()
NSGraphicsContext.restoreGraphicsState()
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
