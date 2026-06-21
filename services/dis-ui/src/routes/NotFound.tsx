import { EmptyState } from '../components/states/EmptyState'

// Not-found route. "Not found" is not one of the four 6.4 state primitives, so it
// reuses EmptyState rather than introducing a fifth primitive.
export function NotFound() {
  return <EmptyState title="Page not found" message="That page does not exist." />
}
