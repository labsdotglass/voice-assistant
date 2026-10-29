// Draws the voice app's icon, the orb as it looks on screen, as icon.png (1024 px).
//   swift audio_helper/make_icon.swift && (make the .icns: see build_app in helper_transport.py)
import AppKit

let size: CGFloat = 1024
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size), pixelsHigh: Int(size), bitsPerSample: 8,
                           samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
let ctx = NSGraphicsContext.current!.cgContext
let space = CGColorSpace(name: CGColorSpace.sRGB)!
func color(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat, _ a: CGFloat = 1) -> CGColor { CGColor(colorSpace: space, components: [r, g, b, a])! }
func radial(_ colors: [CGColor], _ stops: [CGFloat], at c: CGPoint, _ r: CGFloat) {
    let g = CGGradient(colorsSpace: space, colors: colors as CFArray, locations: stops)!
    ctx.drawRadialGradient(g, startCenter: c, startRadius: 0, endCenter: c, endRadius: r, options: [])
}

// The orb as it looks on screen (the AiAvatar orb, listening): a sky-blue disc with white clouds
// banked at the bottom left, a pale rim and a fine grain, on a light tile like the desktop it floats on.
func hex(_ v: Int, _ a: CGFloat = 1) -> CGColor {
    color(CGFloat(v >> 16 & 0xff) / 255, CGFloat(v >> 8 & 0xff) / 255, CGFloat(v & 0xff) / 255, a)
}

// The macOS icon shape: a rounded square inset from the canvas.
let tile = CGRect(x: 100, y: 100, width: 824, height: 824)
let shape = CGPath(roundedRect: tile, cornerWidth: 185, cornerHeight: 185, transform: nil)
ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: -10), blur: 30, color: color(0, 0, 0, 0.3))
ctx.addPath(shape); ctx.setFillColor(hex(0xf4f7fb)); ctx.fillPath()
ctx.restoreGState()
ctx.saveGState()
ctx.addPath(shape); ctx.clip()
let bg = CGGradient(colorsSpace: space, colors: [hex(0xffffff), hex(0xe4ebf4)] as CFArray, locations: [0, 1])!
ctx.drawLinearGradient(bg, start: CGPoint(x: 512, y: 924), end: CGPoint(x: 512, y: 100), options: [])

let c = CGPoint(x: 512, y: 512), r: CGFloat = 300
let disc = CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r)
// Its soft shadow on the tile.
ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: -14), blur: 40, color: hex(0x1e328c, 0.28))
ctx.addEllipse(in: disc); ctx.setFillColor(hex(0x4a93e6)); ctx.fillPath()
ctx.restoreGState()
// The sky: deeper at the top, lighter toward the clouds.
ctx.saveGState()
ctx.addEllipse(in: disc); ctx.clip()
let sky = CGGradient(colorsSpace: space, colors: [hex(0x3f86e0), hex(0x5aa0ec), hex(0x8cc0f2)] as CFArray, locations: [0, 0.55, 1])!
ctx.drawLinearGradient(sky, start: CGPoint(x: c.x + 60, y: c.y + r), end: CGPoint(x: c.x - 80, y: c.y - r), options: [])
// Faint haze drifting across the upper sky.
radial([hex(0xffffff, 0.22), hex(0xffffff, 0)], [0, 1], at: CGPoint(x: c.x - 120, y: c.y + 90), r * 0.55)
radial([hex(0xffffff, 0.15), hex(0xffffff, 0)], [0, 1], at: CGPoint(x: c.x + 150, y: c.y + 10), r * 0.45)
// The cloud bank: puffs of white, densest at the bottom left.
for (dx, dy, k, a) in [(-150.0, -200.0, 0.62, 1.0), (-30.0, -250.0, 0.55, 1.0), (-230.0, -120.0, 0.38, 0.85),
                       (60.0, -190.0, 0.36, 0.8), (-120.0, -110.0, 0.34, 0.75), (150.0, -250.0, 0.40, 0.6)] {
    radial([hex(0xffffff, a), hex(0xffffff, a * 0.75), hex(0xffffff, 0)], [0, 0.45, 1],
           at: CGPoint(x: c.x + dx, y: c.y + dy), r * k)
}
// A fine grain, as on screen.
var rng = SystemRandomNumberGenerator()
for _ in 0 ..< 9000 {
    let a = CGFloat.random(in: 0 ..< 2 * .pi, using: &rng), d = r * sqrt(CGFloat.random(in: 0 ... 1, using: &rng))
    ctx.setFillColor(hex(0xffffff, CGFloat.random(in: 0.03 ... 0.12, using: &rng)))
    ctx.fill(CGRect(x: c.x + d * cos(a), y: c.y + d * sin(a), width: 2, height: 2))
}
// The pale rim.
ctx.addEllipse(in: disc.insetBy(dx: 3, dy: 3)); ctx.setStrokeColor(hex(0xffffff, 0.55)); ctx.setLineWidth(6); ctx.strokePath()
ctx.restoreGState()
ctx.restoreGState()

NSGraphicsContext.current = nil
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon.png"))
