# Chat scrolling: preserve the reader's position

When a reply streams, LocalChatView previously scrolled to the bottom after every text change and message insertion. Reading an earlier answer was interrupted repeatedly.

The chat now follows new content until the user scrolls away from the bottom. Live scrolling temporarily blocks even a previously queued jump. Returning within 80 points of the bottom resumes following. A pause button also lets the reader disable following explicitly; only the return button or sending a new question lifts that pause, scrolling near the bottom does not. Sending a question always returns to the bottom, so the reader sees their question and the start of the answer. The return button shows whether content arrived while paused. The bar is hidden while the chat is empty. The controls do not take focus from the composer. Clearing history resets the policy. State belongs to each view, so two windows sharing the same history can have different reading positions.

The 80-point tolerance is a product choice, not an Apple recommendation. It allows a small offset near the bottom without oscillating between modes. Text growth alone never changes the user's follow preference.

## Platform implementation

The project supports macOS 14. A transparent NSViewRepresentable marker inside the scroll content observes the enclosing NSScrollView. Live-scroll start and end mark a gesture in progress; the reader's position comes from the clip view's bounds-change notifications, so a mouse wheel without phases, keyboard paging, scroller arrows and VoiceOver are seen as well. Only a move up past the tolerance pauses following, so our own jump to the bottom never pauses itself and reply growth during a gesture is not mistaken for leaving the bottom. Notifications are scoped to the actual scroll view; observers are detached on dismantling. Flipped and unflipped document coordinates are handled separately. No new package is required.

Apple documents the enclosing scroll view and live-scroll notification APIs:

- https://developer.apple.com/documentation/appkit/nsview/enclosingscrollview
- https://developer.apple.com/documentation/appkit/nsscrollview/willstartlivescrollnotification
- https://developer.apple.com/documentation/appkit/nsview/boundsdidchangenotification
- https://developer.apple.com/documentation/appkit/nsscrollview/didendlivescrollnotification

## Validation

Nine policy tests cover repeated streamed updates while reading history, pausing by wheel or keyboard without a gesture, an own jump that never pauses itself, cancellation of a queued jump by a live gesture, explicit pause/resume, a pause that survives a touch near the bottom, a stuck gesture cleared by resume, bottom tolerance and unknown geometry, and document coordinate orientation. These tests exercise event sequences and geometry, not the native notification delivery or graphical layout.

Native graphical QA is still required on a Mac. This editing environment cannot launch the macOS application. Do not interpret the policy tests as graphical validation.

## Mac QA checklist

1. In a long conversation, generate a long reply. Scroll upward with a trackpad and mouse wheel while tokens arrive. The visible earlier message should stay in place.
2. Scroll back to the bottom. Following should resume, including after momentum ends.
3. Activate pause (with Full Keyboard Access, from the keyboard), read/select/copy an earlier answer, touch the trackpad near the bottom (the pause must stay), then activate the return button. Verify the composer retains its draft.
4. Try a mouse wheel without phases, scrollbar dragging and arrows, Page Up/Down, VoiceOver navigation and Reduce Motion while tokens arrive. Each move up should pause following.
5. Scroll up, then send a new question: the view returns to the bottom and shows the question and the reply.
6. Test the narrow drawer and a separate wide chat window; confirm controls fit and reading positions are independent.
7. Clear the conversation, reopen a saved history, stop generation and generate an error response. Following should recover normally.

This is an independent change from PR #785. After combining them, repeat the Mac checklist, especially the narrow drawer and chat-clearing confirmation.
