/**
 * Tiny event-based toast bus.
 *
 * Usage:
 *   toast.error('Forbidden');
 *   toast.info('Saved');
 *
 * Mount a single <ToastContainer /> near the root to render messages.
 */
export type ToastKind = 'info' | 'error' | 'success';

export type ToastMessage = {
  id: number;
  kind: ToastKind;
  text: string;
};

type Listener = (msgs: ToastMessage[]) => void;

class ToastBus {
  private msgs: ToastMessage[] = [];
  private listeners = new Set<Listener>();
  private nextId = 1;

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    fn(this.msgs);
    return () => {
      this.listeners.delete(fn);
    };
  }

  private push(kind: ToastKind, text: string) {
    const id = this.nextId++;
    this.msgs = [...this.msgs, { id, kind, text }];
    this.emit();
    setTimeout(() => this.dismiss(id), 5000);
  }

  dismiss(id: number) {
    this.msgs = this.msgs.filter((m) => m.id !== id);
    this.emit();
  }

  private emit() {
    for (const l of this.listeners) l(this.msgs);
  }

  info(text: string) {
    this.push('info', text);
  }
  error(text: string) {
    this.push('error', text);
  }
  success(text: string) {
    this.push('success', text);
  }
}

export const toast = new ToastBus();
