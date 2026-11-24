%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
% Medium Length Professional CV - RESUME CLASS FILE
%
% This template has been downloaded from:
% http://www.LaTeXTemplates.com
%
% This class file defines the structure and design of the template.
%
% Original header:
% Copyright (C) 2010 by Trey Hunner
%
% Copying and distribution of this file, with or without modification,
% are permitted in any medium without royalty provided the copyright
% notice and this notice are preserved. This file is offered as-is,
% without any warranty.
%
% Created by Trey Hunner and modified by www.LaTeXTemplates.com
%
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

\ProvidesClass{resume}[2010/07/10 v0.9 Resume class]

\LoadClass[11pt,letterpaper]{article} % Font size and paper type

% --- Invisible ATS hardening (ApplyAI): same look, but clean Unicode text in the PDF ---
\RequirePackage{cmap}                    % searchable / copyable glyphs
\input{glyphtounicode}\pdfgentounicode=1 % map every glyph (incl. fi/fl ligatures) to Unicode
\ifdefined\pdfinterwordspaceon\pdfinterwordspaceon\fi % real space characters between words
\usepackage[parfill]{parskip} % Remove paragraph indentation
\usepackage{array} % Required for boldface (\bf and \bfseries) tabular columns
\RequirePackage{tabularx}
\RequirePackage{tikz} % round photo (only drawn when \photo{...} is set) % Skills rows wrap instead of running off the page (looks identical when they fit)
\usepackage{ifthen} % Required for ifthenelse statements

\pagestyle{empty} % Suppress page numbers

% Reduce line spacing by 20%
\linespread{0.81}

%----------------------------------------------------------------------------------------
%	HEADINGS COMMANDS: Commands for printing name and address
%----------------------------------------------------------------------------------------

\def \name#1{\def\@name{#1}} % Defines the \name command to set name
\def \@name {} % Sets \@name to empty by default
% Optional round photo at the top right (ApplyAI: DACH / traditional employers only)
\def \photo#1{\def\@photo{#1}}
\def \@photo {}
\newlength{\photosize} \setlength{\photosize}{2.15cm}
\def \@photobox{%
  \begin{tikzpicture}
    \begin{scope}
      \clip (0,0) circle (0.5\photosize);
      \node[inner sep=0pt] at (0,0) {\includegraphics[width=\photosize]{\@photo}};
    \end{scope}
    \draw[line width=0.6pt, black!35] (0,0) circle (0.5\photosize);
  \end{tikzpicture}}
% Optional personal line under the contact line, e.g. date and place of birth (DACH only)
\def \personal#1{\def\@personal{#1}}
\def \@personal {}
% Optional target-role line under the name (ApplyAI, ATS job-title match)
\def \headline#1{\def\@headline{#1}}
\def \@headline {}

% FIXED: Uses math-mode vertical bar for a clean pipe symbol (|)
\def \addressSep {$\mid$}

% One, two or three address lines can be specified
\let \@addressone \relax
\let \@addresstwo \relax
\let \@addressthree \relax

% \address command can be used to set the first, second, and third address (last 2 optional)
\def \address #1{
  \@ifundefined{@addresstwo}{
    \def \@addresstwo {#1}
  }{
  \@ifundefined{@addressthree}{
  \def \@addressthree {#1}
  }{
     \def \@addressone {#1}
  }}
}

% \printaddress is used to style an address line (given as input)
\def \printaddress #1{
  \begingroup
    \def \\ {\ \addressSep\ }
    \centerline{#1}
  \endgroup
  \par
  \addressskip
}

% \printname is used to print the name as a page header
\def \printname {
  \begingroup
    \hfil{\MakeUppercase{\namesize\bf \@name}}\hfil
    \nameskip\break
  \endgroup
  \ifx\@headline\@empty\else
    {\centering\large\@headline\par}\vspace{0.15em}
  \fi
}

%----------------------------------------------------------------------------------------
%	PRINT THE HEADING LINES
%----------------------------------------------------------------------------------------

% Contact lines without the trailing \addressskip (used by both header layouts)
\def \@contactlines #1{%
  \begingroup \def \\ {\ \addressSep\ }%
  \@ifundefined{@addressone}{}{#1{\@addressone}}%
  \@ifundefined{@addresstwo}{}{#1{\@addresstwo}}%
  \@ifundefined{@addressthree}{}{#1{\@addressthree}}%
  \ifx\@personal\@empty\else #1{\@personal}\fi
  \endgroup}
\def \@centerline #1{\centerline{#1}\par\vspace{0.1em}}
\def \@leftline #1{\noindent #1\par\vspace{0.1em}}

\let\ori@document=\document
\renewcommand{\document}{
  \ori@document  % Begin document
  \ifx\@photo\@empty
    % Original design: centered name and contact line
    \printname
    \@contactlines\@centerline
    \vspace{-0.1em}\addressskip
  \else
    % With photo: name and details left-aligned, round photo on the right, centered together
    \noindent
    \begin{minipage}[c]{\dimexpr\textwidth-\photosize-1em\relax}
      {\MakeUppercase{\namesize\bf \@name}}\par\vspace{0.45em}
      \@contactlines\@leftline
    \end{minipage}\hfill
    \begin{minipage}[c]{\photosize}\@photobox\end{minipage}\par
    \vspace{0.25em}
  \fi
}

%----------------------------------------------------------------------------------------
%	SECTION FORMATTING
%----------------------------------------------------------------------------------------

% Defines the rSection environment for the large sections within the CV
\newenvironment{rSection}[1]{ % 1 input argument - section name
  \sectionskip
  \MakeUppercase{\bf #1} % Section title
  \sectionlineskip
  \hrule % Horizontal line
  \begin{list}{}{ % List for each individual item in the section
    \setlength{\leftmargin}{0em} % 0em indentation for Company/School names
  }
  \item[]
}{
  \end{list}
}

%----------------------------------------------------------------------------------------
%	WORK EXPERIENCE FORMATTING
%----------------------------------------------------------------------------------------

\newenvironment{rSubsection}[4]{ % 4 input arguments - company name, date, job title, location
 % 1. Print Header: Company - Position ... Date
 {\bf #1} - {#3} \hfill {#2}%
 %
 % 2. Location Overlay:
 % Anchors to the right margin (end of Date)
 % \llap ensures it aligns right without taking up horizontal space
 % \raisebox{-1.1\baselineskip} drops it down one line to sit next to bullets
 \ifthenelse{\equal{#4}{}}{}{%
    \llap{%
        \smash{%
            \raisebox{-1.1\baselineskip}{#4}%
        }%
    }%
 }%
 % MODIFICATION 1: Removed \smallskip here to pull bullets up closer to the job title
 %
 % 3. Bullet List
 % Right margin increased to 4cm to ensure separation from the Location
 % Right margin: 4cm, or wider when the location needs it, so bullets never touch it
 \settowidth{\@locwidth}{#4}\addtolength{\@locwidth}{1em}%
 \ifdim\@locwidth<4cm \setlength{\@locwidth}{4cm}\fi
 \begin{list}{$\cdot$}{\leftmargin=1.5em \rightmargin=\@locwidth} 
  \itemsep -0.5em \vspace{-0.5em} % Compress items in list
 }{
 \end{list}
 % MODIFICATION 2: Reduced vertical space after the list from 0.5em to 0.1em
 % This is the primary control for spacing BETWEEN positions
 \vspace{\entryskip} 
}

% An entry without bullet points (header line only), same look as rSubsection
\newcommand{\rEntry}[4]{%
 {\bf #1} - {#3} \hfill {#2}%
 \ifthenelse{\equal{#4}{}}{}{\\\hspace*{\fill}#4}%
 \par\vspace{\entryskip}}

\newlength{\@locwidth}

% Space between positions; ApplyAI raises it slightly to fill the page
\def\entryskip{0.1em}

% The below commands define the whitespace after certain things in the document
\def\namesize{\huge} % Size of the name at the top of the document
\def\addressskip{\smallskip} % The space between the two address (or phone/email) lines

% MODIFICATION 3: Tightened up global section spacing
\def\sectionlineskip{\vspace{0.2em}} % The space above the horizontal line for each section
\def\nameskip{\vspace{0.3em}} % The space after your name at the top
\def\sectionskip{\vspace{0.4em}} % The space after the heading section
