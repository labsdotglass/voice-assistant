// Voice Assistant.app's executable: runs the audio helper as its own app in macOS's eyes, so the
// microphone permission is asked for, and listed, as "Voice Assistant" instead of the program
// that started voice mode.
//
//   voice-assistant <helper path> [helper args...]
//
// macOS charges a permission to a process's "responsible" app, normally whoever launched it. So
// this runs twice: first it starts a copy of itself that disclaims that, making this bundle the
// responsible app; that copy then runs the helper as an ordinary child. The helper can be rebuilt
// freely: the permission belongs to this small launcher, which rarely changes.
// stdin, stdout and stderr pass straight through; the exit status comes back.
import Darwin
import Foundation

let args = CommandLine.arguments
guard args.count >= 2 else {
    FileHandle.standardError.write("usage: voice-assistant <helper> [args...]\n".data(using: .utf8)!)
    exit(2)
}

typealias Disclaim = @convention(c) (UnsafeMutablePointer<posix_spawnattr_t?>, Int32) -> Int32

func spawn(_ path: String, _ argv: [String], disclaim: Bool) -> pid_t {
    var attr: posix_spawnattr_t?
    posix_spawnattr_init(&attr)
    defer { posix_spawnattr_destroy(&attr) }
    if disclaim, let sym = dlsym(UnsafeMutableRawPointer(bitPattern: -2), "responsibility_spawnattrs_setdisclaim") {
        _ = unsafeBitCast(sym, to: Disclaim.self)(&attr, 1)
    }
    var cargs = argv.map { strdup($0) } + [nil]
    defer { cargs.forEach { free($0) } }
    var pid: pid_t = 0
    let err = posix_spawn(&pid, path, nil, &attr, &cargs, environ)
    if err != 0 {
        FileHandle.standardError.write("voice-assistant: couldn't start \(path): \(String(cString: strerror(err)))\n".data(using: .utf8)!)
        exit(1)
    }
    return pid
}

var child: pid_t = 0
func forward(_ sig: Int32) { if child > 0 { kill(child, sig) } }
signal(SIGTERM) { forward($0) }
signal(SIGINT) { forward($0) }

if getenv("VOICE_ASSISTANT_DISCLAIMED") == nil {
    setenv("VOICE_ASSISTANT_DISCLAIMED", "1", 1)
    child = spawn(Bundle.main.executablePath ?? args[0], args, disclaim: true)  // the copy that is its own responsible app
} else {
    unsetenv("VOICE_ASSISTANT_DISCLAIMED")
    child = spawn(args[1], Array(args[1...]), disclaim: false)  // the helper, charged to this app
}
var status: Int32 = 0
while waitpid(child, &status, 0) == -1 && errno == EINTR {}
let code = status & 0x7f == 0 ? (status >> 8) & 0xff : 128 + (status & 0x7f)
exit(code)
