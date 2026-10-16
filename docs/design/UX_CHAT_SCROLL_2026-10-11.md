# Chat scrolling: preserve the reader's position

When a reply streams, LocalChatView previously scrolled to the bottom after every text change and message insertion. Reading an earlier answer was interrupted repeatedly.

The chat now follows new content until the reader scrolls away with a live-scroll gesture (trackpad, scroller drag). While the gesture lasts, even a previously queued jump is skipped. When the gesture ends, momentum included, the place of the reader's last own movement decides: more than 80 points above the bottom pauses following, within 80 points resumes it and clears the new-content mark. The reply keeps growing while the gesture suppresses our jumps; that growth after the reader's last movement is not counted, so resting fingers or a downward swipe at the bottom never pause. A pause button also lets the reader disable following explicitly; only the return button or sending a new question lifts that pause, scrolling near the bottom does not. Sending a question always returns to the bottom, so the reader sees their question and the start of the answer. The return button shows whether content arrived while paused. The bar is hidden while the chat is empty. The controls do not take focus from the composer. Clearing history resets the policy. State belongs to each view, so two windows sharing the same history can have different reading positions.

The 80-point tolerance is a product choice, not an Apple recommendation. It also absorbs geometry that is not the reader: the bottom anchor sits a padding above the true document end, and the bottom bar may inset the visible rect. Text growth alone never pauses following.

A mouse wheel without phases and keyboard paging send no live-scroll gesture. They never pause; while following they behave as before this change (the stream keeps the view at the bottom), and the pause button covers them. When following is off after a gesture, each content update reads the position: a reader brought back within 80 points of the bottom by wheel, keys or a window resize follows again. This read can only resume, never pause. Inferring a pause from clip-view movement was tried and rejected: our own jump, lazy row measurement, insets and clamping move the origin too, and each geometric rule paused the chat by itself.

## Platform implementation

The project supports macOS 14. A transparent NSViewRepresentable marker inside the scroll content observes the enclosing NSScrollView. Live-scroll start and end mark a gesture in progress. The marker (not SwiftUI state) samples the distance from the bottom and the document height at the gesture start and at every live-scroll movement; at the end, the reader's last own movement is measured against the bottom as it was at the start (reply growth during the gesture is subtracted), and that is the only automatic pause decision. Unknown geometry keeps the previous decision. Clip-view movement outside gestures is not observed. If the window closes or resigns key mid-gesture, the movement already made decides on the next main-loop turn; if the observed scroll view goes away mid-gesture, the gesture latch is released without scrolling and the previous decision stays. Notifications are scoped to the actual scroll view; observers are detached on dismantling. Flipped and unflipped document coordinates are handled separately. No new package is required.

Apple documents the enclosing scroll view and live-scroll notification APIs:

- https://developer.apple.com/documentation/appkit/nsview/enclosingscrollview
- https://developer.apple.com/documentation/appkit/nsscrollview/willstartlivescrollnotification
- https://developer.apple.com/documentation/appkit/nsscrollview/didlivescrollnotification
- https://developer.apple.com/documentation/appkit/nsscrollview/didendlivescrollnotification

## Validation

Sixteen policy tests cover repeated streamed updates while reading history, cancellation of a queued jump by a live gesture, a gesture ending near the bottom (anchor padding), a gesture back to the bottom clearing the new-content mark, content growth that never decides by itself, reply growth during a gesture that is not the reader leaving, the last own movement deciding, an end without a start, a position seen without a gesture that only resumes and respects the pause button and a running gesture, explicit pause/resume, a pause that survives a gesture at the bottom, a cancelled gesture that keeps the previous decision, a stuck gesture cleared by resume, bottom tolerance and unknown geometry that keeps the decision, and document coordinate orientation. These tests exercise event sequences and geometry, not the native notification delivery or graphical layout.

Native graphical QA is still required on a Mac. This editing environment cannot launch the macOS application. Do not interpret the policy tests as graphical validation.

## Mac QA checklist

1. In a long conversation, generate a long reply. Scroll upward with a trackpad and mouse wheel while tokens arrive. The visible earlier message should stay in place.
2. Scroll back to the bottom. Following should resume, including after momentum ends.
3. Activate pause (with Full Keyboard Access, from the keyboard), read/select/copy an earlier answer, touch the trackpad near the bottom (the pause must stay), then activate the return button. Verify the composer retains its draft.
4. Drag the scrollbar up while tokens arrive: following pauses. Return to the bottom with End or a mouse wheel: following resumes with the next token. A mouse wheel without phases and Page Up/Down keep following as before; the pause button stops it.
5. Scroll up, then send a new question: the view returns to the bottom and shows the question and the reply.
6. At the bottom while tokens arrive, without touching anything: the bar must keep showing the pause button (no self-pause). Then rest two fingers on the trackpad for three seconds, and swipe down with momentum: following must stay. With a reply paused above the bottom, resize the window without bringing the bottom into view: following must not resume. Switch to another app in the middle of a trackpad gesture and back: if the bar shows the pause button, the view must keep following new tokens.
7. Test the narrow drawer and a separate wide chat window; confirm controls fit and reading positions are independent.
8. Clear the conversation, reopen a saved history, stop generation and generate an error response. Following should recover normally.

This is an independent change from PR #785. After combining them, repeat the Mac checklist, especially the narrow drawer and chat-clearing confirmation.
