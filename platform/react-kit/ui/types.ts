import type { ReactNode } from 'react';

export type SharedProps = { testId?: string; id?: string };
export type Gap = 1 | 2 | 3 | 4 | 6 | 8;
export type ScreenProps = SharedProps & { pageId: string; children: ReactNode; width?: 'mobile' | 'content' | 'wide'; title?: string };
export type StackProps = SharedProps & { children: ReactNode; gap?: Gap };
export type GridProps = SharedProps & { children: ReactNode; columns?: 1 | 2 | 3; gap?: Gap };
export type InlineProps = SharedProps & {
  children: ReactNode; gap?: 1 | 2 | 3 | 4 | 6; align?: 'start' | 'center' | 'end'; justify?: 'start' | 'between' | 'end';
};
export type PanelProps = SharedProps & { children: ReactNode; title?: string; tone?: 'default' | 'subtle' | 'brand' };
export type TextProps = SharedProps & {
  children: ReactNode; as?: 'span' | 'p' | 'h1' | 'h2' | 'h3'; size?: 'sm' | 'md' | 'lg';
  tone?: 'default' | 'muted' | 'brand' | 'danger';
};
export type ButtonProps = SharedProps & {
  label: string; onClick?: () => void; kind?: 'primary' | 'secondary' | 'danger'; disabled?: boolean; type?: 'button' | 'submit';
};
export type InputProps = SharedProps & {
  label: string; value: string; onChange: (value: string) => void; type?: 'text' | 'number' | 'email' | 'tel';
  hint?: string; error?: string; required?: boolean; disabled?: boolean; min?: number; max?: number; placeholder?: string;
};
export type CheckboxProps = SharedProps & {
  label: string; checked: boolean; onChange: (checked: boolean) => void; disabled?: boolean; required?: boolean;
};
export type ChoiceOption = { value: string; label: string };
export type SelectProps = SharedProps & {
  label: string; value: string; onChange: (value: string) => void; options: ChoiceOption[]; disabled?: boolean;
};
export type RadioGroupProps = SharedProps & {
  label: string; value: string; onChange: (value: string) => void; options: ChoiceOption[];
};
export type AlertProps = SharedProps & { message: string; title?: string; tone?: 'info' | 'success' | 'warning' | 'danger' };
export type StepperProps = SharedProps & { steps: { id: string; label: string }[]; current: string };
export type SummaryProps = SharedProps & { title?: string; items: { label: string; value: string }[] };
export type AssetImageProps = SharedProps & { src: string; alt: string; width?: number; height?: number };
