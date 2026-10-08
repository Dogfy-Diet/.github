export function shippingCost(total, country) {
  if (country !== 'ES') {
    throw new Error(`No enviamos a ${country}`)
  }
  if (total >= 50) {
    return 0
  }
  return 4.95
}

export function estimatedDays(country) {
  if (country === 'ES') {
    return 2
  }
  return 5
}
