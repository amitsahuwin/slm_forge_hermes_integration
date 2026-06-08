import { useState } from 'react';
import SynthesizeModal from './SynthesizeModal';

type SynthesizeButtonProps = {
  /** Name of the source dataset to expand. */
  dataset: string;
  /** Total seed-record count, displayed in the modal subtitle. */
  count: number;
  /**
   * Visual variant. `compact` is for inline use in list rows next to a Link;
   * `header` is for the larger detail-page button.
   */
  variant?: 'compact' | 'header';
  className?: string;
};

/**
 * Trigger for the dataset-synthesis modal.
 *
 * IMPORTANT: this component calls ``e.preventDefault()`` and
 * ``e.stopPropagation()`` on click so it can be safely nested inside a
 * ``react-router-dom`` ``<Link>`` (used on the Datasets list page) without
 * triggering navigation when the button is clicked.
 */
export default function SynthesizeButton({
  dataset,
  count,
  variant = 'compact',
  className = '',
}: SynthesizeButtonProps) {
  const [open, setOpen] = useState(false);

  const base =
    variant === 'header'
      ? 'rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500'
      : 'rounded-md border border-emerald-700/60 bg-emerald-950/40 px-2.5 py-1 font-mono text-[11px] text-emerald-300 hover:border-emerald-500 hover:bg-emerald-900/40';

  return (
    <>
      <button
        type="button"
        onClick={(e) => {
          // Prevent the surrounding <Link> on the list page from navigating.
          e.preventDefault();
          e.stopPropagation();
          setOpen(true);
        }}
        className={`${base} ${className}`.trim()}
        title="Synthesize new dataset from this one"
      >
        Synthesize
      </button>
      <SynthesizeModal
        open={open}
        sourceDataset={dataset}
        sourceCount={count}
        onClose={() => setOpen(false)}
      />
    </>
  );
}
