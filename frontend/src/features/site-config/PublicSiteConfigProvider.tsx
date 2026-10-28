import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { AccountMembershipServicePort, PublicSiteConfig } from "../../shared/api/accountMembership";
import type { AppMode } from "../../shared/api/config";

export const DEFAULT_SITE_TITLE = "Hao AI 创作工作台";
export const DEFAULT_SITE_TAGLINE = "登录后查看你的剧集与协作项目。";
export const APPLICATION_HOME_HREF = "/";

export interface PublicSiteConfigSource {
  readonly mode: AppMode;
  readonly accountMembership?: Pick<AccountMembershipServicePort, "getPublicConfig">;
}

export interface PublicSitePresentation {
  readonly siteTitle: string;
  readonly siteTagline: string;
  readonly tutorialHref: string;
  readonly tutorialOpensInNewTab: boolean;
}

const DEFAULT_PRESENTATION: PublicSitePresentation = {
  siteTitle: DEFAULT_SITE_TITLE,
  siteTagline: DEFAULT_SITE_TAGLINE,
  tutorialHref: APPLICATION_HOME_HREF,
  tutorialOpensInNewTab: false,
};

const PublicSiteConfigContext = createContext<PublicSitePresentation>(DEFAULT_PRESENTATION);

function nonBlankText(value: string, fallback: string): string {
  const trimmed = value.trim();
  return trimmed === "" ? fallback : trimmed;
}

export function resolvePublicTutorialLink(value: string): Pick<
  PublicSitePresentation,
  "tutorialHref" | "tutorialOpensInNewTab"
> {
  const candidate = value.trim();
  if (candidate === "" || /[\u0000-\u001f\u007f]/.test(candidate)) {
    return { tutorialHref: APPLICATION_HOME_HREF, tutorialOpensInNewTab: false };
  }

  try {
    const url = new URL(candidate);
    if ((url.protocol !== "https:" && url.protocol !== "http:")
      || url.hostname === ""
      || url.username !== ""
      || url.password !== "") {
      return { tutorialHref: APPLICATION_HOME_HREF, tutorialOpensInNewTab: false };
    }
    return { tutorialHref: url.href, tutorialOpensInNewTab: true };
  } catch {
    return { tutorialHref: APPLICATION_HOME_HREF, tutorialOpensInNewTab: false };
  }
}

function presentationFromConfig(config: PublicSiteConfig): PublicSitePresentation {
  return {
    siteTitle: nonBlankText(config.site_title, DEFAULT_SITE_TITLE),
    siteTagline: nonBlankText(config.site_tagline, DEFAULT_SITE_TAGLINE),
    ...resolvePublicTutorialLink(config.tutorial_url),
  };
}

interface PublicSiteConfigProviderProps {
  services: PublicSiteConfigSource | null;
  children: ReactNode;
}

export function PublicSiteConfigProvider({ services, children }: PublicSiteConfigProviderProps) {
  const servicesRef = useRef(services);
  servicesRef.current = services;
  const requestSequence = useRef(0);
  const [loaded, setLoaded] = useState(() => ({ owner: services, presentation: DEFAULT_PRESENTATION }));
  const presentation = loaded.owner === services ? loaded.presentation : DEFAULT_PRESENTATION;

  useLayoutEffect(() => {
    const previousTitle = document.title;
    document.title = presentation.siteTitle;
    return () => {
      if (document.title === presentation.siteTitle) {
        document.title = previousTitle;
      }
    };
  }, [presentation.siteTitle]);

  useEffect(() => {
    const owner = services;
    const accountMembership = owner?.mode === "api" ? owner.accountMembership : undefined;
    const requestId = ++requestSequence.current;
    const controller = new AbortController();
    let active = true;
    const isCurrent = () => active
      && requestSequence.current === requestId
      && servicesRef.current === owner
      && !controller.signal.aborted;

    setLoaded({ owner, presentation: DEFAULT_PRESENTATION });
    if (accountMembership !== undefined) {
      void Promise.resolve().then(() => accountMembership.getPublicConfig(controller.signal)).then((config) => {
        if (isCurrent()) {
          setLoaded({ owner, presentation: presentationFromConfig(config) });
        }
      }).catch(() => {
        if (isCurrent()) {
          setLoaded({ owner, presentation: DEFAULT_PRESENTATION });
        }
      });
    }

    return () => {
      active = false;
      controller.abort();
      if (requestSequence.current === requestId) {
        requestSequence.current += 1;
      }
    };
  }, [services]);

  return (
    <PublicSiteConfigContext.Provider value={presentation}>
      {children}
    </PublicSiteConfigContext.Provider>
  );
}

export function usePublicSiteConfig(): PublicSitePresentation {
  return useContext(PublicSiteConfigContext);
}

interface PublicSiteTutorialLinkProps {
  className?: string;
}

export function PublicSiteTutorialLink({ className }: PublicSiteTutorialLinkProps) {
  const { tutorialHref, tutorialOpensInNewTab } = usePublicSiteConfig();
  return (
    <a
      aria-label="查看使用教程"
      className={className}
      href={tutorialHref}
      rel={tutorialOpensInNewTab ? "noopener noreferrer" : undefined}
      target={tutorialOpensInNewTab ? "_blank" : undefined}
    >
      查看使用教程
    </a>
  );
}
