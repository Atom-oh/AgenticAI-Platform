export const COMPONENT_NAMES = [
  'Screen', 'Stack', 'Grid', 'Inline', 'Panel', 'Text', 'Button', 'Input', 'Checkbox',
  'Select', 'RadioGroup', 'Alert', 'Stepper', 'Summary', 'AssetImage',
] as const;

export type ComponentName = typeof COMPONENT_NAMES[number];
export function isComponentName(value: unknown): value is ComponentName {
  return typeof value === 'string' && (COMPONENT_NAMES as readonly string[]).includes(value);
}
