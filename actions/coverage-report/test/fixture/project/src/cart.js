export function subtotal(items) {
  return items.reduce((sum, item) => sum + item.price * item.qty, 0)
}

export function itemCount(items) {
  return items.reduce((n, item) => n + item.qty, 0)
}

export function applyDiscount(total, code) {
  if (code === 'WELCOME10') {
    return total * 0.9
  }
  return total
}
