# Chat scrolling: preserve the reader's position

When a reply streams, LocalChatView previously scrolled to the bottom after every text change and message insertion. Reading an earlier answer was interrupted repeatedly.

The chat now follows new content until the user scrolls up, by any amount: one wheel notch is enough. Live scrolling temporarily blocks even a previously queued jump. Scrolling back down to within 80 points of the bottom resumes following and clears the new-content mark. A pause button also lets the reader disable following explicitly; only the return button or sending a new question lifts that pause, scrolling near the bottom does not. Sending a question always returns to the bottom, so the reader sees their question and the start of the answer. The return button shows whether content arrived while paused. The bar is hidden while the chat is empty. The controls do not take focus from the composer. Clearing history resets the policy. State belongs to each view, so two windows sharing the same history can have different reading positions.

The 80-point tolerance is a product choice, not an Apple recommendation; it only decides when a move down counts as a return. Text growth alone never changes the user's follow preference.

## Platform implementation

The project supports macOS 14. A transparent NSViewRepresentable marker inside the scroll content observes the enclosing NSScrollView. Live-scroll start and end mark a gesture in progress; the reader's position comes from the clip view's bounds-change notifications, so a mouse wheel without phases, keyboard paging, scroller arrows and VoiceOver are seen as well. Direction decides: any move up that leaves the bottom pauses, only a move down into the tolerance resumes. Our own jump only moves down, so it never pauses itself. Reply growth adds content below and does not move the clip view origin, so it sends no event at all. When the document shrinks (a window resize reflows the text), the clip view clamps its origin up to the new bottom: that event moves up but lands at distance 0 and changes nothing. If the observed scroll view goes away mid-gesture, the gesture latch is released without scrolling. Notifications are scoped to the actual scroll view; observers are detached on dismantling. Flipped and unflipped document coordinates are handled separately. No new package is required.

Apple documents the enclosing scroll view and live-scroll notification APIs:

- https://developer.apple.com/documentation/appkit/nsview/enclosingscrollview
- https://developer.apple.com/documentation/appkit/nsscrollview/willstartlivescrollnotification
- https://developer.apple.com/documentation/appkit/nsview/boundsdidchangenotification
- https://developer.apple.com/documentation/appkit/nsscrollview/didendlivescrollnotification

## Validation

Thirteen policy tests cover repeated streamed updates while reading history, pausing by wheel or keyboard without a gesture, a single wheel notch inside the tolerance, an own jump that never pauses itself, a shrinking-document clamp, cancellation of a queued jump by a live gesture, explicit pause/resume, a pause that survives a touch near the bottom, the new-content mark cleared at the bottom while paused, a stuck gesture cleared by resume, bottom tolerance and unknown geometry, move direction in flipped and unflipped coordinates, and document coordinate orientation. These tests exercise event sequences and geometry, not the native notification delivery or graphical layout.

Native graphical QA is still required on a Mac. This editing environment cannot launch the macOS application. Do not interpret the policy tests as graphical validation.

## Mac QA checklist

1. In a long conversation, generate a long reply. Scroll upward with a trackpad and mouse wheel while tokens arrive. The visible earlier message should stay in place.
2. Scroll back to the bottom. Following should resume, including after momentum ends.
3. Activate pause (with Full Keyboard Access, from the keyboard), read/select/copy an earlier answer, touch the trackpad near the bottom (the pause must stay), then activate the return button. Verify the composer retains its draft.
4. Try a mouse wheel without phases, scrollbar dragging and arrows, Page Up/Down, VoiceOver navigation and Reduce Motion while tokens arrive. Each move up should pause following.
5. Scroll up, then send a new question: the view returns to the bottom and shows the question and the reply.
6. With a reply paused above the bottom, resize the window so the text reflows: following must not resume by itself.
7. Test the narrow drawer and a separate wide chat window; confirm controls fit and reading positions are independent.
8. Clear the conversation, reopen a saved history, stop generation and generate an error response. Following should recover normally.

This is an independent change from PR #785. After combining them, repeat the Mac checklist, especially the narrow drawer and chat-clearing confirmation.
