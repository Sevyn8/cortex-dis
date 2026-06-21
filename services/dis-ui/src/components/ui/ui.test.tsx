import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { Badge } from './badge'
import { Button } from './button'
import { Card, CardContent, CardTitle } from './card'
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from './dialog'
import { Input } from './input'
import { Label } from './label'
import { Select } from './select'
import { Table, TableBody, TableCell, TableRow } from './table'

describe('ui primitives (base-nova)', () => {
  it('Button renders and applies variant classes', () => {
    const { rerender } = render(<Button>Go</Button>)
    const btn = screen.getByRole('button', { name: 'Go' })
    expect(btn.className).toContain('bg-primary')
    rerender(<Button variant="outline">Go</Button>)
    expect(screen.getByRole('button', { name: 'Go' }).className).toContain('border-border')
  })

  it('Badge renders with variant classes', () => {
    const { rerender } = render(<Badge>new</Badge>)
    expect(screen.getByText('new').className).toContain('bg-primary')
    rerender(<Badge variant="destructive">err</Badge>)
    expect(screen.getByText('err').className).toContain('text-destructive')
  })

  it('Card renders children with the card surface', () => {
    render(
      <Card>
        <CardTitle>Title</CardTitle>
        <CardContent>Body</CardContent>
      </Card>,
    )
    expect(screen.getByText('Title')).toBeInTheDocument()
    expect(screen.getByText('Body')).toBeInTheDocument()
  })

  it('Input applies the dense h-8 treatment', () => {
    render(<Input placeholder="name" />)
    expect(screen.getByPlaceholderText('name').className).toContain('h-8')
  })

  it('Table renders a row and cell', () => {
    render(
      <Table>
        <TableBody>
          <TableRow>
            <TableCell>cell</TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    )
    expect(screen.getByText('cell')).toBeInTheDocument()
  })

  it('Label renders its text and htmlFor', () => {
    render(<Label htmlFor="x">Name</Label>)
    const label = screen.getByText('Name')
    expect(label).toBeInTheDocument()
    expect(label).toHaveAttribute('for', 'x')
  })

  it('Select renders its options', () => {
    render(
      <Select aria-label="kind">
        <option value="a">A</option>
        <option value="b">B</option>
      </Select>,
    )
    expect(screen.getByRole('combobox', { name: 'kind' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'A' })).toBeInTheDocument()
  })

  it('Dialog opens on trigger click and shows its title', async () => {
    const user = userEvent.setup()
    render(
      <Dialog>
        <DialogTrigger>Open</DialogTrigger>
        <DialogContent showCloseButton={false}>
          <DialogTitle>Confirm</DialogTitle>
        </DialogContent>
      </Dialog>,
    )
    expect(screen.queryByText('Confirm')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Open' }))
    expect(await screen.findByText('Confirm')).toBeInTheDocument()
  })
})
