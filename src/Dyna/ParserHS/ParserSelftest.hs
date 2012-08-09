{-# LANGUAGE TemplateHaskell #-}
{-# LANGUAGE Rank2Types #-}
{-# LANGUAGE OverloadedStrings #-}

-- TODO:
--   Writing these is still too hard, Template Haskell and the REPL
--     notwithstanding.
--
--   Test.Framework.TH appears not to understand comments at the
--   moment, and parses right through them.

-- XXX Cabal doesn't understand Main-Is in quite the right way
-- module Dyna.ParserHS.ParserSelftest where

import           Control.Applicative ((<*))
import           Data.ByteString (ByteString)
import           Data.Foldable (toList)
import           Data.Monoid (mempty)
import qualified Data.Sequence                       as S
import           Data.String
import           Test.Framework.Providers.HUnit
import           Test.Framework.TH
import           Test.HUnit
import           Text.Trifecta

import           Dyna.Test.Trifecta
import           Dyna.ParserHS.Parser


term :: ByteString -> Spanned Term
term = unsafeParse dterm

case_basicAtom = e @=? (term "foo")
 where e = TFunctor "foo" [] :~ Span (Columns 0 0) (Columns 3 3) "foo"

case_basicAtomTWS = e @=? (term "foo ")
 where e =  TFunctor "foo" [] :~ Span (Columns 0 0) (Columns 4 4) "foo "

case_basicFunctor = e @=? (term sfb)
 where
  e =  TFunctor "foo"
         [TFunctor "bar" [] :~ Span (Columns 4 4) (Columns 7 7) sfb
         ]
        :~ Span (Columns 0 0) (Columns 8 8) sfb

  sfb :: (IsString s) => s
  sfb = "foo(bar)"

case_nestedFunctorsWithArgs = e @=? (term st)
 where
  e = TFunctor "foo"
        [TFunctor "bar" [] :~ Span (Columns 4 4) (Columns 7 7) st
        ,TVar "X" :~ Span (Columns 8 8) (Columns 9 9) st
        ,TFunctor "bif" [] :~ Span (Columns 10 10) (Columns 15 15) st
        ,TFunctor "baz"
           [TFunctor "quux" [] :~ Span (Columns 20 20) (Columns 24 24) st
           ,TVar "Y" :~ Span (Columns 25 25) (Columns 26 26) st
           ]
          :~ Span (Columns 16 16) (Columns 27 27) st
        ]
       :~ Span (Columns 0 0) (Columns 28 28) st

  st :: (IsString s) => s
  st = "foo(bar,X,bif(),baz(quux,Y))"


case_basicFunctorTWS = e @=? (term sfb)
 where
  e = TFunctor "foo"
       [TFunctor "bar" [] :~ Span (Columns 5 5) (Columns 9 9) sfb
       ] :~ Span (Columns 0 0) (Columns 10 10) sfb

  sfb :: (IsString s) => s
  sfb = "foo (bar )"

case_basicFunctorNL = e @=? (term sfb)
 where
  e = TFunctor "foo"
       [TFunctor "bar" [] :~ Span (Lines 1 1 5 1) (Lines 1 5 9 5) "(bar )"
       ] :~ Span (Columns 0 0) (Lines 1 6 10 6) "foo\n"

  sfb :: (IsString s) => s
  sfb = "foo\n(bar )"

case_colonFunctor = e @=? (term pvv)
 where
  e = TFunctor "possible"
        [TFunctor ":"
           [TVar "Var" :~ Span (Columns 9 9) (Columns 12 12) pvv
           ,TVar "Val" :~ Span (Columns 13 13) (Columns 16 16) pvv
           ]
          :~ Span (Columns 9 9) (Columns 16 16) pvv
        ]
       :~ Span (Columns 0 0) (Columns 17 17) "possible(Var:Val)"
  pvv = "possible(Var:Val)"

case_failIncompleteExpr = checkParseFail dterm "foo +"
    [(Right (Columns 4 4), "expected: \"(\", end of input")]

progline :: ByteString -> Spanned Line
progline = unsafeParse dline

proglines :: ByteString -> [Spanned Line]
proglines = unsafeParse dlines

case_ruleSimple = e @=? (progline sr)
 where
  e  = LRule (Rule (TFunctor "goal" [] :~ Span (Columns 0 0) (Columns 5 5) sr)
                   "+=" 
                   []
                   (TFunctor "1" [] :~ Span (Columns 8 8) (Columns 10 10) sr)
            :~ Span (Columns 0 0) (Columns 10 10) sr)
           :~ Span (Columns 0 0) (Columns 10 10) sr
  sr = "goal += 1 ."
  
case_ruleExpr = e @=? (progline sr)
 where
  e  = LRule (Rule (TFunctor "goal" [] :~ Span (Columns 0 0) (Columns 5 5) sr)
                   "+=" 
                   []
                   (TFunctor "+"
                      [TFunctor "foo" [] :~ Span (Columns 8 8) (Columns 12 12) sr
                      ,TFunctor "bar" [] :~ Span (Columns 14 14) (Columns 18 18) sr
                      ]
                     :~ Span (Columns 8 8) (Columns 18 18) sr
                   )
                  :~ Span (Columns 0 0) (Columns 18 18) sr)
                 :~ Span (Columns 0 0) (Columns 18 18) sr
  sr = "goal += foo + bar ."

case_ruleDotExpr = e @=? (progline sr)
 where
  e  = LRule (Rule (TFunctor "goal" [] :~ Span (Columns 0 0) (Columns 5 5) sr)
                   "+=" 
                   []
                   (TFunctor "."
                      [TFunctor "foo" [] :~ Span (Columns 8 8) (Columns 11 11) sr
                      ,TFunctor "bar" [] :~ Span (Columns 12 12) (Columns 15 15) sr
                      ]
                     :~ Span (Columns 8 8) (Columns 15 15) sr
                   )
                  :~ Span (Columns 0 0) (Columns 15 15) sr)
                 :~ Span (Columns 0 0) (Columns 15 15) sr
  sr = "goal += foo.bar."

case_ruleComma = e @=? (progline sr)
 where
  e = LRule (Rule (TFunctor "foo" [] :~ Span (Columns 0 0) (Columns 4 4) sr)
                  "+="
                  [TFunctor "bar"
                     [TVar "X" :~ Span (Columns 11 11) (Columns 12 12) sr]
                    :~ Span (Columns 7 7) (Columns 13 13) sr
                  ,TFunctor "baz"
                     [TVar "X" :~ Span (Columns 19 19) (Columns 20 20) sr]
                    :~ Span (Columns 15 15) (Columns 21 21) sr
                  ]
                  (TVar "X" :~ Span (Columns 23 23) (Columns 24 24) sr)
                 :~ Span (Columns 0 0) (Columns 24 24) sr)
                :~ Span (Columns 0 0) (Columns 24 24) sr
  sr = "foo += bar(X), baz(X), X."

case_ruleKeywordsComma = e @=? (progline sr)
 where
  e  = LRule (Rule (TFunctor "foo" [] :~ Span (Columns 0 0) (Columns 4 4) sr)
                   "="
                   [TFunctor "is"
                      [TVar "X" :~ Span (Columns 21 21) (Columns 23 23) sr
                      ,TFunctor "baz"
                         [TVar "Y" :~ Span (Columns 30 30) (Columns 31 31) sr]
                        :~ Span (Columns 26 26) (Columns 32 32) sr
                      ]
                     :~ Span (Columns 21 21) (Columns 32 32) sr
                   ,TFunctor "is"
                      [TVar "Y" :~ Span (Columns 34 34) (Columns 36 36) sr
                      ,TFunctor "3" [] :~ Span (Columns 39 39) (Columns 41 41) sr
                      ]
                     :~ Span (Columns 34 34) (Columns 41 41) sr
                   ]
                   (TFunctor "new"
                      [TVar "X" :~ Span (Columns 10 10) (Columns 12 12) sr]
                     :~ Span (Columns 6 6) (Columns 12 12) sr)
                  :~ Span (Columns 0 0) (Columns 41 41) sr)
                 :~ Span (Columns 0 0) (Columns 41 41) sr
  sr = "foo = new X whenever X is baz(Y), Y is 3 ."

-- XXX It takes a while to parse this one.  Why?
case_rules = e @=? (proglines sr)
 where
  e = [ LRule (Rule (TFunctor "goal" [] :~ Span (Columns 0 0) (Columns 5 5) sr)
                     "+="
                     []
                     (TFunctor "1" [] :~ Span (Columns 8 8) (Columns 9 9) sr)
                    :~ Span (Columns 0 0) (Columns 9 9) sr)
                   :~ Span (Columns 0 0) (Columns 9 9) sr
      , LRule (Rule (TFunctor "goal" [] :~ Span (Columns 11 11) (Columns 16 16) sr)
                    "+="
                    []
                    (TFunctor "2" [] :~ Span (Columns 19 19) (Columns 20 20) sr)
                   :~ Span (Columns 11 11) (Columns 20 20) sr)
                  :~ Span (Columns 11 11) (Columns 20 20) sr
      ]
  sr = "goal += 1. goal += 2."

-- XXX It takes a while to parse this one.  Why?
case_rulesDotExpr = e @=? (proglines sr)
 where
  e  = [ LRule (Rule (TFunctor "goal" [] :~ Span (Columns 0 0) (Columns 5 5) sr)
                      "+=" 
                      []
                      (TFunctor "."
                         [TFunctor "foo" [] :~ Span (Columns 8 8) (Columns 11 11) sr
                         ,TFunctor "bar" [] :~ Span (Columns 12 12) (Columns 15 15) sr
                         ]
                        :~ Span (Columns 8 8) (Columns 15 15) sr
                      )
                     :~ Span (Columns 0 0) (Columns 15 15) sr)
                    :~ Span (Columns 0 0) (Columns 15 15) sr
       , LRule (Rule (TFunctor "goal" [] :~ Span (Columns 17 17) (Columns 22 22) sr)
                      "+=" 
                      []
                      (TFunctor "1" [] :~ Span (Columns 25 25) (Columns 26 26) sr)
                     :~ Span (Columns 17 17) (Columns 26 26) sr)
                    :~ Span (Columns 17 17) (Columns 26 26) sr
       ]
  sr = "goal += foo.bar. goal += 1."


main :: IO ()
main = $(defaultMainGenerator)

{-
runParser :: (Show a) => (forall r . Language (Parser r String) a) -> B.ByteString -> Result TermDoc a
runParser p = parseByteString (dynafy p <* eof) M.mempty 

testParser :: (Show a) => (forall r . Language (Parser r String) a) -> String -> IO ()
testParser p = parseTest (dynafy p <* eof)

testDyna :: (Show a) => (forall r . Language (Parser r String) a) -> String -> Result TermDoc a
testDyna p i = runParser p (BU.fromString i) 

cs r e = case r of
           Success w s | S.null w -> assertEqual "XXX" e s
           _ -> assertBool "XXX" False
-}
