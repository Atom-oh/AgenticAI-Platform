import { createReactLoader } from '../../portal-react/client';
export { usageSnippet } from '../../portal-react/examples/snippets';

export type ReactCatalog = {
  id: string;
  version: string;
  label: string;
  hash: string;
  components: { name: string; description: string; props: Record<string, string>; variationAxes: string[] }[];
};

// One verified bundle per page, shared by full previews and lazy gallery cards.
const loader = createReactLoader();
export const loadReactCatalog = (): Promise<ReactCatalog> => loader.catalog();
export const loadReactDocument = () => loader.document();
