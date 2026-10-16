import SwiftUI

#if os(macOS)
/// Layout is based on measured chip widths, not a fixed number per row.
struct SourceChipFlow: Layout {
    var spacing: CGFloat = 4

    static func frames(sizes: [CGSize], width: CGFloat, spacing: CGFloat = 4) -> [CGRect] {
        let available = max(1, width.isFinite ? width : 1)
        let gap = max(0, spacing)
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        return sizes.map { size in
            let w = min(available, max(0, size.width))
            let h = max(0, size.height)
            if x > 0, x + w > available {
                x = 0
                y += rowHeight + gap
                rowHeight = 0
            }
            let frame = CGRect(x: x, y: y, width: w, height: h)
            x += w + gap
            rowHeight = max(rowHeight, h)
            return frame
        }
    }

    private func frames(proposal: ProposedViewSize, subviews: Subviews) -> [CGRect] {
        let natural = subviews.map { $0.sizeThatFits(.unspecified) }
        let naturalWidth = natural.reduce(0) { $0 + $1.width } + CGFloat(max(0, natural.count - 1)) * spacing
        let proposedWidth = proposal.width ?? naturalWidth
        let available = max(1, proposedWidth.isFinite ? proposedWidth : naturalWidth)
        let sizes = subviews.map { $0.sizeThatFits(ProposedViewSize(width: available, height: nil)) }
        return Self.frames(sizes: sizes, width: available, spacing: spacing)
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let frames = frames(proposal: proposal, subviews: subviews)
        return CGSize(width: frames.map(\.maxX).max() ?? 0,
                      height: frames.map(\.maxY).max() ?? 0)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let frames = frames(proposal: ProposedViewSize(width: bounds.width, height: nil), subviews: subviews)
        for (view, frame) in zip(subviews, frames) {
            view.place(at: CGPoint(x: bounds.minX + frame.minX, y: bounds.minY + frame.minY),
                       anchor: .topLeading,
                       proposal: ProposedViewSize(width: frame.width, height: frame.height))
        }
    }
}
#endif
